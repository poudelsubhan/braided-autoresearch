"""Consistency verifier: cross-lineage replication as a pure function.

No LLM in the decision itself — only the classifier's labels (class_assign
events) are consumed. A class is REPLICATED iff it has accepted members in
>= k lineages that are pairwise independent:

  independent(a, b): neither commit is an ancestor of the other in the full
  DAG, AND the class did not reach both via a shared ancestor commit — i.e.
  no single class member is an ancestor of (or equal to) both. Two branches
  that both inherit the change from a common-ancestor commit are one
  discovery, not two.

Emits replication_tag events (deduped: re-verification only appends when a
class's independent-member set grew) and feeds the tree view's ◆ markers and
the proposer's replicated_classes_digest.
"""

from __future__ import annotations

from itertools import combinations
from pathlib import Path

from braided.graph import Graph
from braided.ledger import ClassAssignEvent, Ledger, ReplicationTagEvent


def _independent(graph: Graph, sha_a: str, sha_b: str, members: set[str]) -> bool:
    if sha_a == sha_b:
        return False
    if graph.is_ancestor(sha_a, sha_b) or graph.is_ancestor(sha_b, sha_a):
        return False
    # shared-via-common-ancestor: some member of the same class sits below both
    for m in members:
        if m in (sha_a, sha_b):
            continue
        if graph.is_ancestor(m, sha_a) and graph.is_ancestor(m, sha_b):
            return False
    return True


def _max_independent_set(graph: Graph, members: set[str]) -> list[str]:
    """Greedy max independent subset (class sizes are tiny; exactness not
    worth the complexity — greedy can only under-count, never over-count)."""
    chosen: list[str] = []
    for sha in sorted(members):
        if all(_independent(graph, sha, c, members) for c in chosen):
            chosen.append(sha)
    return chosen


def check_replication(run_dir: str | Path, k: int) -> list[ReplicationTagEvent]:
    """Recompute replication for every class; append new/updated tags to the
    ledger. Returns the tags appended by THIS call."""
    run_dir = Path(run_dir)
    graph = Graph(run_dir / "repo")
    ledger = Ledger(run_dir)

    assigns: dict[str, list[ClassAssignEvent]] = {}
    for e in ledger.events():
        if isinstance(e, ClassAssignEvent):
            assigns.setdefault(e.class_id, []).append(e)

    already: dict[str, set[str]] = {}
    for e in ledger.events():
        if isinstance(e, ReplicationTagEvent):
            already[e.class_id] = set(e.member_shas)

    node_branch = {n.sha: n.branch for n in graph.nodes()}
    appended = []
    for class_id, events in assigns.items():
        members = {e.sha for e in events if e.sha in node_branch}
        independent = _max_independent_set(graph, members)
        if len(independent) < k:
            continue
        if already.get(class_id) == set(independent):
            continue  # no growth since last tag
        tag = ReplicationTagEvent(
            class_id=class_id,
            class_summary=events[-1].class_summary,
            member_shas=sorted(independent),
            lineages=sorted({node_branch.get(s) or "?" for s in independent}),
        )
        ledger.append(tag)
        appended.append(tag)
    return appended
