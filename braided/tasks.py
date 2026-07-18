"""Task registry + init-task: materialize a fresh experiment repo for a task.

Harness repo vs experiment repo: everything here CREATES experiment repos under
runs/<run-id>/repo/ — a separate git history the search loop operates on. The
harness repo is never touched.
"""

from __future__ import annotations

import json
import shutil
import statistics
import subprocess
import time
import urllib.request
from pathlib import Path

from braided.config import RunConfig, SearchConfig, TaskConfig, load_task_config
from braided.runner import run_scorer

HARNESS_ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = HARNESS_ROOT / "tasks"

TASK_DIRS = {
    "cpu-optimize": TASKS_DIR / "cpu-optimize",
    "nanogpt-shakespeare": TASKS_DIR / "nanogpt",
}

TINYSHAKESPEARE_URL = (
    "https://raw.githubusercontent.com/karpathy/char-rnn/master/data/tinyshakespeare/input.txt"
)

EXPERIMENT_GITIGNORE = "__pycache__/\n*.pyc\nckpt.pt\n"


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {repo}: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _prepare_nanogpt(repo: Path, run_dir: Path) -> None:
    """Download tinyshakespeare and split it. Split logic is the anti-cheat:
    train/val land in the repo; the held-out slice lives OUTSIDE the repo."""
    text = urllib.request.urlopen(TINYSHAKESPEARE_URL, timeout=60).read().decode()
    n = len(text)
    train, val, heldout = text[: int(0.85 * n)], text[int(0.85 * n) : int(0.925 * n)], text[int(0.925 * n) :]
    (repo / "train.txt").write_text(train)
    (repo / "val.txt").write_text(val)
    (run_dir / "heldout.txt").write_text(heldout)
    (repo / "meta.json").write_text(json.dumps({"chars": sorted(set(text))}))


PREPARE_HOOKS = {"nanogpt-shakespeare": _prepare_nanogpt}


def heldout_env(run_dir: str | Path) -> dict[str, str]:
    """Extra env for the private scorer. Kept in one place so nothing else
    ever mentions the held-out file."""
    return {"BRAIDED_HELDOUT_FILE": str(Path(run_dir).resolve() / "heldout.txt")}


def init_task(
    task_name: str,
    runs_root: str | Path = "runs",
    run_id: str | None = None,
    calibrate: int = 3,
    budget_seconds: float | None = None,
    search: SearchConfig | None = None,
    seed: int = 0,
) -> Path:
    """Create runs/<run-id>/ with an experiment repo, run.yaml, and a baseline
    calibration (scorer run `calibrate` times on the unmodified template).
    Returns the run dir."""
    if task_name not in TASK_DIRS:
        raise ValueError(f"unknown task {task_name!r}; known: {sorted(TASK_DIRS)}")
    task_dir = TASK_DIRS[task_name]
    task: TaskConfig = load_task_config(task_dir / "task.yaml")
    if budget_seconds is not None:
        task = task.model_copy(update={"budget_seconds": budget_seconds})

    run_id = run_id or f"{task_name}-{time.strftime('%Y%m%d-%H%M%S')}"
    run_dir = Path(runs_root) / run_id
    repo = run_dir / "repo"
    if repo.exists():
        raise FileExistsError(f"{repo} already exists")
    repo.mkdir(parents=True)
    (run_dir / "logs").mkdir()

    shutil.copytree(task_dir / "template", repo, dirs_exist_ok=True)
    (repo / ".gitignore").write_text(EXPERIMENT_GITIGNORE)
    hook = PREPARE_HOOKS.get(task_name)
    if hook:
        hook(repo, run_dir)

    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "braided@localhost")
    git(repo, "config", "user.name", "braided")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "baseline: task template")

    run_config = RunConfig(
        task=task, search=search or SearchConfig(), seed=seed, output_dir=str(run_dir)
    )
    run_config.save(run_dir / "run.yaml")

    scores = []
    for i in range(calibrate):
        result = run_scorer(repo, task, run_dir / "logs", log_prefix=f"baseline-{i}")
        if not result.ok:
            raise RuntimeError(
                f"baseline scorer failed ({result.failure_kind}): {result.detail} "
                f"[see {result.stderr_path}]"
            )
        scores.append(result.score)
    baseline = {
        "task": task_name,
        "scores": scores,
        "mean": statistics.mean(scores),
        "std": statistics.stdev(scores) if len(scores) > 1 else 0.0,
        "budget_seconds": task.budget_seconds,
    }
    (run_dir / "baseline.json").write_text(json.dumps(baseline, indent=2))
    return run_dir
