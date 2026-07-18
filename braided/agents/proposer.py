"""Proposer agent: one focused patch per call.

Receives task description + editable code + lineage context (rationale trail,
failure digest, sibling digest, replicated classes) and returns
{rationale, patch}. Malformed responses raise ProposalError — the caller logs
a failed attempt and moves on; never fatal.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from braided.agents import llm
from braided.config import TaskConfig
from braided.runner import find_protected_violations

MAX_FILE_CHARS = 24_000

SYSTEM = """\
You are the proposer in an automated research loop that optimizes code against a
fixed scorer. You will be shown the task, the current code, and context about
what this lineage and its siblings have already tried.

Rules:
- Propose exactly ONE focused change per response. Small, mechanistic, testable.
- The rationale must state the MECHANISM you expect ("replace linear-scan
  counting with a dict because inserts dominate the profile"), never restate
  the diff.
- Never touch protected files. Never try to game the scorer; it is enforced
  and correctness-checked. Score gains must come from real improvements.
- The score is measured within a FIXED wall-clock budget: prefer efficiency
  over scale. A change that only helps with more compute is a bad proposal.
- Do not repeat an idea listed in the failure digest; vary the mechanism.
- Respond with a single JSON object, no markdown fences, no prose outside it:
  {"rationale": "<one or two sentences>", "patch": "<unified diff>"}
- Patch format: standard unified diff with `--- a/<path>` / `+++ b/<path>`
  headers. Context lines must EXACTLY match the current file content shown to
  you (hunk line counts are recomputed, so exact counts don't matter, but
  context lines do). Keep hunks small and include 3 unchanged context lines
  BEFORE and AFTER each hunk's changes.
"""


class ProposalError(RuntimeError):
    pass


@dataclass
class Proposal:
    rationale: str
    patch: str


def editable_files(repo: str | Path, task: TaskConfig) -> dict[str, str]:
    """Tracked, text, non-protected, reasonably-sized files the proposer sees."""
    from braided.graph import git

    repo = Path(repo)
    files = {}
    for rel in git(repo, "ls-files").splitlines():
        if find_protected_violations([rel], task.protected_paths):
            continue
        if rel == ".gitignore":
            continue
        path = repo / rel
        try:
            text = path.read_text()
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        if len(text) > MAX_FILE_CHARS:
            continue
        files[rel] = text
    return files


def build_prompt(
    repo: str | Path,
    task: TaskConfig,
    rationale_trail: str,
    failure_digest: str,
    sibling_digest: str,
    replicated_digest: str,
    direction: str = "",
    parent_score: float | None = None,
    accept_threshold: float | None = None,
) -> str:
    files = editable_files(repo, task)
    file_blocks = "\n\n".join(
        f"### {name}\n```\n{content}\n```" for name, content in sorted(files.items())
    )
    better = "lower" if task.direction == "min" else "higher"
    parts = [
        f"# Task: {task.name}",
        f"Public metric: {task.metric_name} ({better} is better). "
        f"Wall-clock budget per scoring run: {task.budget_seconds:.0f}s.",
        f"Protected files (do NOT touch): {', '.join(task.protected_paths)}",
    ]
    if parent_score is not None:
        parts.append(f"Current score of this lineage: {parent_score:.4f}.")
    if accept_threshold is not None:
        parts.append(
            f"A change is only accepted if it improves the score by more than "
            f"{accept_threshold:.4f} (the measured noise floor)."
        )
    if direction:
        parts.append(f"## This lineage's exploration direction\n{direction}")
    parts += [
        f"## Current code (editable files)\n{file_blocks}",
        f"## Accepted changes on this lineage (most recent first)\n{rationale_trail}",
        f"## Recent failed/rejected attempts on this lineage — do not repeat these\n{failure_digest}",
        f"## Sibling lineages\n{sibling_digest}",
        f"## Replicated findings across lineages\n{replicated_digest}",
        "Propose the single next change. JSON only.",
    ]
    return "\n\n".join(parts)


def parse_response(text: str) -> Proposal:
    """Lenient JSON extraction: strip fences, find the outermost object."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.MULTILINE).strip()
    start = t.find("{")
    if start == -1:
        raise ProposalError(f"no JSON object in response: {t[:200]!r}")
    # walk to the matching close brace, respecting strings
    depth, in_str, esc, end = 0, False, False, None
    for i, ch in enumerate(t[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        raise ProposalError("unbalanced JSON object in response")
    try:
        obj = json.loads(t[start:end])
    except json.JSONDecodeError as e:
        raise ProposalError(f"JSON parse failed: {e}") from e
    rationale = str(obj.get("rationale", "")).strip()
    patch = str(obj.get("patch", ""))
    if not rationale or not patch.strip():
        raise ProposalError("response missing rationale or patch")
    return Proposal(rationale=rationale, patch=patch)


def propose(
    repo: str | Path,
    task: TaskConfig,
    rationale_trail: str,
    failure_digest: str,
    sibling_digest: str = "(no sibling branches)",
    replicated_digest: str = "(no replicated classes yet)",
    direction: str = "",
    parent_score: float | None = None,
    accept_threshold: float | None = None,
) -> Proposal:
    from braided.privacy import assert_no_private_leak

    prompt = assert_no_private_leak(
        build_prompt(
            repo, task, rationale_trail, failure_digest, sibling_digest,
            replicated_digest, direction, parent_score, accept_threshold,
        ),
        task,
    )
    return parse_response(llm.complete(prompt, system=SYSTEM))
