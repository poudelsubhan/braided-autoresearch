"""Sandbox runner: execute a task scorer in an experiment worktree.

Process isolation + protected-path checks only (v1). The production upgrade is
a real sandbox (E2B/Modal); for a hackathon a subprocess with a hard timeout,
its own cwd, and captured output is enough — the anti-cheat lives in the
scorers (correctness asserts, held-out splits) and in the path checks here.
"""

from __future__ import annotations

import fnmatch
import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from braided.config import TaskConfig

FailureKind = Literal["timeout", "crash", "invalid-output", "protected-path-violation"]

GRACE_SECONDS = 30.0  # on top of the task's internal wall-clock budget


@dataclass
class ScoreResult:
    status: Literal["ok", "failed"]
    score: float | None = None
    failure_kind: FailureKind | None = None
    duration: float = 0.0
    stdout_path: str | None = None
    stderr_path: str | None = None
    detail: str = ""
    extra: dict = field(default_factory=dict)  # any other keys from the score JSON

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def find_protected_violations(changed_paths: list[str], protected: list[str]) -> list[str]:
    """Repo-relative changed paths that match any protected glob."""
    violations = []
    for p in changed_paths:
        norm = p.replace("\\", "/").lstrip("./")
        for pattern in protected:
            if fnmatch.fnmatch(norm, pattern) or norm == pattern.rstrip("/"):
                violations.append(p)
                break
    return violations


def parse_score_output(stdout: str) -> tuple[float | None, dict, str]:
    """Parse the last JSON-looking stdout line into (score, extras, error)."""
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "score" in obj:
            try:
                score = float(obj["score"])
            except (TypeError, ValueError):
                return None, {}, f"non-numeric score: {obj['score']!r}"
            extras = {k: v for k, v in obj.items() if k != "score"}
            return score, extras, ""
    return None, {}, "no JSON line with a 'score' key on stdout"


def run_scorer(
    worktree: str | Path,
    task: TaskConfig,
    log_dir: str | Path,
    log_prefix: str = "attempt",
    command: str | None = None,
    env: dict[str, str] | None = None,
) -> ScoreResult:
    """Run a scorer command (default: the public scorer) inside the worktree.

    stdout/stderr are persisted under log_dir as <prefix>.stdout / <prefix>.stderr
    so the ledger can reference them.
    """
    worktree = Path(worktree)
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    cmd = command or task.scorer_command
    timeout = task.budget_seconds + GRACE_SECONDS

    import os
    import sys

    # Scorer commands say `python ...`; make that resolve to the harness venv
    # interpreter regardless of the user's shell PATH.
    venv_bin = str(Path(sys.executable).parent)
    full_env = {
        **os.environ,
        "PATH": venv_bin + os.pathsep + os.environ.get("PATH", ""),
        "BRAIDED_BUDGET_SECONDS": str(task.budget_seconds),
    }
    if env:
        full_env.update(env)

    stdout_path = log_dir / f"{log_prefix}.stdout"
    stderr_path = log_dir / f"{log_prefix}.stderr"
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=worktree,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=full_env,
        )
        duration = time.perf_counter() - start
        stdout_path.write_text(proc.stdout)
        stderr_path.write_text(proc.stderr)
        if proc.returncode != 0:
            return ScoreResult(
                status="failed",
                failure_kind="crash",
                duration=duration,
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                detail=f"exit code {proc.returncode}: {proc.stderr.strip()[-500:]}",
            )
        score, extras, err = parse_score_output(proc.stdout)
        if score is None:
            return ScoreResult(
                status="failed",
                failure_kind="invalid-output",
                duration=duration,
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                detail=err,
            )
        return ScoreResult(
            status="ok",
            score=score,
            duration=duration,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            extra=extras,
        )
    except subprocess.TimeoutExpired as e:
        duration = time.perf_counter() - start
        stdout_path.write_text((e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""))
        stderr_path.write_text((e.stderr or b"").decode() if isinstance(e.stderr, bytes) else (e.stderr or ""))
        return ScoreResult(
            status="failed",
            failure_kind="timeout",
            duration=duration,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            detail=f"exceeded {timeout:.0f}s (budget {task.budget_seconds:.0f}s + {GRACE_SECONDS:.0f}s grace)",
        )
