"""Append-only run ledger (ledger.jsonl) + cross-check against the git DAG.

Every event is timestamped and carries the run id. The ledger is the query
surface for agent context and reports; git is the source of truth for
topology. `verify_ledger` proves they agree.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Iterator, Literal, Union

from pydantic import BaseModel, Field

from braided.graph import Graph


class BaseEvent(BaseModel):
    ts: float = Field(default_factory=time.time)
    run_id: str = ""


class AttemptEvent(BaseEvent):
    type: Literal["attempt"] = "attempt"
    attempt_index: int
    branch: str
    parent_sha: str
    sha: str | None = None  # set iff accepted (committed)
    rationale: str = ""
    diff_summary: str = ""
    result: Literal["accepted", "rejected", "failed"]
    score: float | None = None
    failure_kind: str | None = None
    duration: float = 0.0
    detail: str = ""


class MergeAttemptEvent(BaseEvent):
    type: Literal["merge_attempt"] = "merge_attempt"
    attempt_index: int
    parents: list[str]
    base_sha: str = ""
    branch: str | None = None  # new arm name iff composed
    sha: str | None = None  # set iff composed (committed)
    rationale: str = ""
    result: Literal["compose", "interfere", "fail"]
    score: float | None = None
    parent_scores: list[float | None] = Field(default_factory=list)
    failure_kind: str | None = None
    detail: str = ""


class ReplicationTagEvent(BaseEvent):
    type: Literal["replication_tag"] = "replication_tag"
    class_id: str
    class_summary: str = ""
    member_shas: list[str] = Field(default_factory=list)
    lineages: list[str] = Field(default_factory=list)  # branch names


class ClassAssignEvent(BaseEvent):
    """Phase 4 groundwork: classifier's change-class label for an accepted node."""
    type: Literal["class_assign"] = "class_assign"
    sha: str
    class_id: str
    class_summary: str = ""


class BranchEvent(BaseEvent):
    """Branch lifecycle: birth (fork) / prune, with the direction hint."""
    type: Literal["branch"] = "branch"
    branch: str
    action: Literal["fork", "prune"]
    from_sha: str = ""
    direction: str = ""  # persistent exploration hint for this lineage
    detail: str = ""


Event = Union[AttemptEvent, MergeAttemptEvent, ReplicationTagEvent, ClassAssignEvent, BranchEvent]

_EVENT_TYPES = {
    "attempt": AttemptEvent,
    "merge_attempt": MergeAttemptEvent,
    "replication_tag": ReplicationTagEvent,
    "class_assign": ClassAssignEvent,
    "branch": BranchEvent,
}


class Ledger:
    def __init__(self, run_dir: str | Path, run_id: str | None = None):
        self.run_dir = Path(run_dir)
        self.path = self.run_dir / "ledger.jsonl"
        self.run_id = run_id or self.run_dir.name

    def append(self, event: BaseEvent) -> None:
        if not event.run_id:
            event.run_id = self.run_id
        with open(self.path, "a") as f:
            f.write(event.model_dump_json() + "\n")

    def events(self) -> Iterator[Event]:
        if not self.path.exists():
            return
        with open(self.path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                cls = _EVENT_TYPES.get(obj.get("type"))
                if cls is None:
                    continue
                yield cls.model_validate(obj)

    def attempts(self) -> list[AttemptEvent]:
        return [e for e in self.events() if isinstance(e, AttemptEvent)]

    def merge_attempts(self) -> list[MergeAttemptEvent]:
        return [e for e in self.events() if isinstance(e, MergeAttemptEvent)]

    def next_attempt_index(self) -> int:
        indices = [
            e.attempt_index
            for e in self.events()
            if isinstance(e, (AttemptEvent, MergeAttemptEvent))
        ]
        return (max(indices) + 1) if indices else 0


def verify_ledger(run_dir: str | Path) -> list[str]:
    """Walk the DAG; confirm every accepted (non-root) node has a matching
    ledger event and a score note. Returns a list of problems (empty = OK)."""
    run_dir = Path(run_dir)
    graph = Graph(run_dir / "repo")
    ledger = Ledger(run_dir)

    accepted_shas = {e.sha for e in ledger.attempts() if e.result == "accepted" and e.sha}
    composed_shas = {e.sha for e in ledger.merge_attempts() if e.result == "compose" and e.sha}
    root = graph.root()

    problems = []
    for node in graph.nodes():
        if node.sha == root:
            continue
        if node.is_merge:
            if node.sha not in composed_shas:
                problems.append(f"merge node {node.sha[:10]} has no compose merge_attempt event")
        elif node.sha not in accepted_shas:
            problems.append(f"node {node.sha[:10]} has no accepted attempt event")
        if node.score is None:
            problems.append(f"node {node.sha[:10]} has no score note")

    dag_shas = {n.sha for n in graph.nodes()}
    for sha in accepted_shas | composed_shas:
        if sha not in dag_shas:
            problems.append(f"ledger references sha {sha[:10]} not present in the DAG")
    return problems
