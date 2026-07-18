"""Merge daemon: agent-mediated semantic merges of two lineages.

Pair selection: top two branches (by head score) whose accepted-change sets
are most disjoint. Disjointness heuristic: 1 - |files(A) ∩ files(B)| /
|files(A) ∪ files(B)| over the files each lineage changed since the common
ancestor, with hunk overlap as tiebreak. Rationale: file/hunk overlap is a
cheap proxy for semantic collision — two lineages that edited disjoint code
almost always compose mechanically, so the agent's capacity is spent where
textual merging would fail anyway; identical-file pairs still merge when
scores justify it, they just rank behind.

The merge agent sees both lineages' diffs-from-common-ancestor + rationale
trails and writes ONE combined patch against the common ancestor — a
rewrite, not a textual merge. Outcomes:
  compose   — beats both parents → two-parent merge node, new active arm
  interfere — scores but beats ≤1 parent → rejected, interference recorded
  fail      — invalid patch / crash / protected path
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from braided.agents import llm
from braided.agents.proposer import parse_response  # same JSON envelope
from braided.graph import Graph

SYSTEM = """\
You are the merge agent in an automated research loop. Two independent
lineages both improved the same codebase in different ways. You will see the
common-ancestor versions of the files each lineage touched, each lineage's
full diff from that ancestor, and their rationale trails.

Write ONE combined patch AGAINST THE COMMON ANCESTOR that composes both
lineages' improvements. This is a semantic rewrite, not a textual merge:
where the changes touch the same code, reconcile them by intent — keep the
mechanism of each that still applies, drop what the other supersedes.

Rules:
- The patch must apply to the COMMON ANCESTOR content shown (not to either
  lineage's version).
- Preserve exact observable behavior contracts stated in the code/README.
- Never touch protected files.
- Respond with a single JSON object, no prose:
  {"rationale": "<what composes, what conflicts and how resolved>",
   "patch": "<unified diff against the ancestor>"}
"""

PROMPT = """\
# Task: {task_name} — combine two improvement lineages
Protected files (do NOT touch): {protected}

## Common ancestor content (the code your patch must apply to)
{ancestor_files}

## Lineage A ({branch_a}, score {score_a:.4f})
Rationales (oldest→newest):
{trail_a}
Diff from ancestor:
```
{diff_a}
```

## Lineage B ({branch_b}, score {score_b:.4f})
Rationales (oldest→newest):
{trail_b}
Diff from ancestor:
```
{diff_b}
```

Write the single combined patch against the ancestor. JSON only.
"""


@dataclass
class MergePlan:
    branch_a: str
    branch_b: str
    sha_a: str
    sha_b: str
    base_sha: str
    disjointness: float


def _changed_files(graph: Graph, base: str, sha: str) -> set[str]:
    out = graph._git("diff", "--name-only", base, sha)
    return {l for l in out.splitlines() if l}


def pick_merge_pair(graph: Graph, task, min_score_gain: float = 0.0) -> MergePlan | None:
    """Top two branches, ranked by score, most-disjoint pair preferred.
    Only branches whose head improves on the common ancestor qualify."""
    heads = graph.branches()
    scored = []
    for branch, sha in heads.items():
        s = graph.get_score(sha)
        if s is not None:
            scored.append((branch, sha, s))
    if len(scored) < 2:
        return None
    scored.sort(key=lambda t: t[2], reverse=(task.direction == "max"))

    best_plan, best_key = None, None
    for i in range(len(scored)):
        for j in range(i + 1, len(scored)):
            (ba, sa, sca), (bb, sb, scb) = scored[i], scored[j]
            base = graph.merge_base(sa, sb)
            if base in (sa, sb):
                continue  # one is ancestor of the other: nothing to compose
            base_score = graph.get_score(base)
            if base_score is not None and (
                task.improvement(sca, base_score) <= min_score_gain
                or task.improvement(scb, base_score) <= min_score_gain
            ):
                continue  # a lineage with nothing accepted since base adds nothing
            fa, fb = _changed_files(graph, base, sa), _changed_files(graph, base, sb)
            if not fa or not fb:
                continue
            union, inter = fa | fb, fa & fb
            disjointness = 1.0 - len(inter) / len(union)
            # rank: prefer high combined score first (i is already best), then
            # disjointness; only pairs within the top-3 heads considered
            if j > 2:
                continue
            key = (disjointness, -(i + j))
            if best_key is None or key > best_key:
                best_key = key
                best_plan = MergePlan(ba, bb, sa, sb, base, disjointness)
        if i >= 2:
            break
    return best_plan


def _rationales(graph: Graph, base: str, sha: str) -> str:
    trail = []
    for s in graph.lineage(sha):
        if s == base or graph.is_ancestor(s, base):
            break
        r = graph.get_meta(s).get("rationale")
        if r:
            trail.append(r)
    return "\n".join(f"- {r}" for r in reversed(trail)) or "(none)"


def _ancestor_files(graph: Graph, plan: MergePlan, protected: list[str]) -> str:
    from braided.runner import find_protected_violations

    files = _changed_files(graph, plan.base_sha, plan.sha_a) | _changed_files(
        graph, plan.base_sha, plan.sha_b
    )
    blocks = []
    for f in sorted(files):
        if find_protected_violations([f], protected):
            continue
        content = graph._git("show", f"{plan.base_sha}:{f}", check=False)
        blocks.append(f"### {f}\n```\n{content}\n```")
    return "\n\n".join(blocks)


def propose_merge(graph: Graph, task, plan: MergePlan):
    """Returns a Proposal (rationale + combined patch against the ancestor)."""
    from braided.privacy import assert_no_private_leak

    prompt = PROMPT.format(
        task_name=task.name,
        protected=", ".join(task.protected_paths),
        ancestor_files=_ancestor_files(graph, plan, task.protected_paths),
        branch_a=plan.branch_a, score_a=graph.get_score(plan.sha_a) or 0.0,
        trail_a=_rationales(graph, plan.base_sha, plan.sha_a),
        diff_a=graph.diff_from(plan.base_sha, plan.sha_a)[:12000],
        branch_b=plan.branch_b, score_b=graph.get_score(plan.sha_b) or 0.0,
        trail_b=_rationales(graph, plan.base_sha, plan.sha_b),
        diff_b=graph.diff_from(plan.base_sha, plan.sha_b)[:12000],
    )
    assert_no_private_leak(prompt, task)
    return parse_response(llm.complete(prompt, system=SYSTEM, max_tokens=16000))
