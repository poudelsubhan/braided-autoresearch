"""Strings the agents actually see, computed from graph + ledger.

Nothing here may ever include the private held-out scorer command or the
held-out data path — enforced by test_no_private_leak_in_context.
"""

from __future__ import annotations

from braided.graph import Graph
from braided.ledger import Ledger, ReplicationTagEvent


def rationale_trail(graph: Graph, branch: str, n: int = 5) -> str:
    """Last n accepted rationales on this lineage (most recent first)."""
    lines = []
    for sha in graph.lineage(graph.head_of(branch))[:-1]:  # exclude root
        meta = graph.get_meta(sha)
        rationale = meta.get("rationale")
        if rationale:
            score = graph.get_score(sha)
            score_s = f" [score {score:.4f}]" if score is not None else ""
            lines.append(f"- {rationale}{score_s}")
        if len(lines) >= n:
            break
    return "\n".join(lines) if lines else "(no accepted changes yet on this lineage)"


def sibling_digest(graph: Graph, ledger: Ledger, branch: str) -> str:
    """One line per other active branch: current score + last rationale."""
    lines = []
    for name, head in sorted(graph.branches().items()):
        if name == branch:
            continue
        score = graph.get_score(head)
        meta = graph.get_meta(head)
        rationale = meta.get("rationale", "")
        score_s = f"{score:.4f}" if score is not None else "?"
        lines.append(f"- {name}: score {score_s} — last change: {rationale or '(baseline)'}")
    return "\n".join(lines) if lines else "(no sibling branches)"


def failure_digest(ledger: Ledger, branch: str, n: int = 5) -> str:
    """Recent failed/rejected attempts on this lineage, so the proposer stops
    repeating dead ends."""
    lines = []
    for e in reversed(ledger.attempts()):
        if e.branch != branch or e.result == "accepted":
            continue
        why = e.failure_kind or e.result
        score_s = f", scored {e.score:.4f}" if e.score is not None else ""
        lines.append(f"- [{why}{score_s}] {e.rationale or e.diff_summary or '(no rationale)'}"
                     + (f" — {e.detail[:160]}" if e.detail else ""))
        if len(lines) >= n:
            break
    return "\n".join(lines) if lines else "(no failed attempts yet on this branch)"


def replicated_classes_digest(ledger: Ledger) -> str:
    """Which change-classes replicated across independent lineages (Phase 4)."""
    tags = [e for e in ledger.events() if isinstance(e, ReplicationTagEvent)]
    if not tags:
        return "(no replicated classes yet)"
    lines = []
    for t in tags:
        lines.append(
            f"- {t.class_id}: {t.class_summary} — replicated in {len(t.lineages)} "
            f"lineages ({', '.join(t.lineages)})"
        )
    return "\n".join(lines)
