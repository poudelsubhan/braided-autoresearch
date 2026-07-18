"""Run engine: the propose → apply → run → score → accept/revert cycle.

Strategies (greedy/tree/braided) decide WHERE the next attempt happens; the
engine owns HOW an attempt executes and is the only writer of graph commits and
ledger events during a run. Resume is derived state: attempt numbering comes
from the ledger, code state from the git DAG, so an interrupted run restarts
idempotently.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Callable

from braided import context
from braided.accept import is_accepted, load_baseline, threshold_for_run
from braided.agents.llm import LLMError
from braided.agents.proposer import Proposal, ProposalError, propose
from braided.config import RunConfig
from braided.graph import Graph, PatchError, ProtectedPathViolation
from braided.ledger import AttemptEvent, Ledger
from braided.runner import run_scorer

ProposerFn = Callable[..., Proposal]  # signature of agents.proposer.propose


class Engine:
    def __init__(self, cfg: RunConfig, proposer_fn: ProposerFn = propose,
                 status_fn: Callable[[str], None] = print):
        self.cfg = cfg
        self.task = cfg.task
        self.run_dir = Path(cfg.output_dir)
        self.repo = self.run_dir / "repo"
        self.graph = Graph(self.repo)
        self.ledger = Ledger(self.run_dir)
        self.baseline = load_baseline(self.run_dir)
        self.threshold = threshold_for_run(cfg)
        self.proposer_fn = proposer_fn
        self.status_fn = status_fn
        self.branch_directions: dict[str, str] = self._load_directions()
        # Guards ledger appends, attempt-index allocation, and ref/notes
        # mutations when parallel workers pull different arms concurrently.
        self.lock = threading.RLock()

    # -- derived state (resume-safe) ------------------------------------------

    def _load_directions(self) -> dict[str, str]:
        directions: dict[str, str] = {}
        from braided.ledger import BranchEvent

        for e in self.ledger.events():
            if isinstance(e, BranchEvent) and e.action == "fork" and e.direction:
                directions[e.branch] = e.direction
        return directions

    def attempts_done(self) -> int:
        return self.ledger.next_attempt_index()

    def best_node(self) -> tuple[str, float]:
        """(sha, score) of the best-scored node in the DAG."""
        best_sha, best_score = None, None
        for node in self.graph.nodes():
            if node.score is None:
                continue
            if best_score is None or self.task.better(node.score, best_score):
                best_sha, best_score = node.sha, node.score
        assert best_sha is not None, "DAG has no scored nodes (init-task not run?)"
        return best_sha, best_score

    def score_of(self, sha: str) -> float:
        """Score of sha, falling back to nearest scored first-parent ancestor."""
        for ancestor in self.graph.lineage(sha):
            s = self.graph.get_score(ancestor)
            if s is not None:
                return s
        raise RuntimeError(f"no scored ancestor for {sha}")

    # -- the attempt cycle ----------------------------------------------------

    def execute_attempt(self, branch: str, worktree: Path | None = None,
                        idx: int | None = None) -> AttemptEvent:
        """One propose→apply→score→accept/revert cycle on `branch`.

        `worktree`: an isolated checkout of `branch` (from `git worktree add`)
        for parallel pulls; defaults to the main experiment worktree. Commits
        and notes go through the shared repo either way, serialized by lock.
        """
        wt_graph = Graph(worktree) if worktree else self.graph
        with self.lock:
            if idx is None:
                idx = self.ledger.next_attempt_index()
            if worktree is None:
                wt_graph.checkout(branch)
            parent_sha = wt_graph.head_of(branch)
        wt_path = worktree or self.repo
        parent_score = self.score_of(parent_sha)

        event = AttemptEvent(
            attempt_index=idx, branch=branch, parent_sha=parent_sha,
            result="failed", score=None,
        )

        try:
            proposal = self.proposer_fn(
                repo=wt_path,
                task=self.task,
                rationale_trail=context.rationale_trail(self.graph, branch),
                failure_digest=context.failure_digest(self.ledger, branch),
                sibling_digest=context.sibling_digest(self.graph, self.ledger, branch),
                replicated_digest=context.replicated_classes_digest(self.ledger),
                direction=self.branch_directions.get(branch, ""),
                parent_score=parent_score,
                accept_threshold=self.threshold,
            )
        except ProposalError as e:
            event.failure_kind = "bad-proposal"
            event.detail = str(e)[:500]
            return self._finish(event, parent_score)
        except LLMError as e:
            event.failure_kind = "llm-error"
            event.detail = str(e)[:500]
            return self._finish(event, parent_score)

        event.rationale = proposal.rationale
        event.diff_summary = self._diff_summary(proposal.patch)
        (self.run_dir / "logs" / f"attempt-{idx:04d}.patch").write_text(proposal.patch)

        try:
            wt_graph.apply_patch(proposal.patch, protected=self.task.protected_paths)
        except ProtectedPathViolation as e:
            event.failure_kind = "protected-path-violation"
            event.detail = str(e)[:500]
            return self._finish(event, parent_score)
        except PatchError as e:
            event.failure_kind = "patch-apply-failed"
            event.detail = str(e)[:500]
            return self._finish(event, parent_score)

        result = run_scorer(
            wt_path, self.task, self.run_dir / "logs", log_prefix=f"attempt-{idx:04d}"
        )
        event.duration = result.duration

        if not result.ok:
            event.failure_kind = result.failure_kind
            event.detail = result.detail[:500]
            wt_graph.checkout(branch)  # revert working tree
            return self._finish(event, parent_score)

        event.score = result.score
        with self.lock:
            if is_accepted(self.task, result.score, parent_score, self.threshold):
                sha = wt_graph.commit_all(
                    f"[{idx:04d}] {proposal.rationale[:72]}",
                    branch, score=result.score, rationale=proposal.rationale,
                )
                event.sha = sha
                event.result = "accepted"
            else:
                event.result = "rejected"
                wt_graph.checkout(branch)  # revert
        return self._finish(event, parent_score)

    def _finish(self, event: AttemptEvent, parent_score: float) -> AttemptEvent:
        with self.lock:
            self.ledger.append(event)
        _, best = self.best_node()
        outcome = event.result if event.result != "failed" else f"failed:{event.failure_kind}"
        score_s = f"{event.score:.4f}" if event.score is not None else "-"
        self.status_fn(
            f"[{event.attempt_index:03d}] {event.branch:<8} {outcome:<28} "
            f"score={score_s} parent={parent_score:.4f} best={best:.4f}"
        )
        return event

    def _diff_summary(self, patch: str) -> str:
        from braided.graph import patch_changed_paths

        paths = patch_changed_paths(patch)
        plus = sum(1 for l in patch.splitlines() if l.startswith("+") and not l.startswith("+++"))
        minus = sum(1 for l in patch.splitlines() if l.startswith("-") and not l.startswith("---"))
        return f"{', '.join(paths)} (+{plus}/-{minus})"

    # -- heldout (never in agent context) -------------------------------------

    def run_heldout(self, sha: str, log_prefix: str) -> float | None:
        """Check out sha, run the private scorer. Returns score or None."""
        from braided.tasks import heldout_env

        self.graph.checkout(sha)
        result = run_scorer(
            self.repo, self.task, self.run_dir / "logs", log_prefix=log_prefix,
            command=self.task.heldout_scorer_command, env=heldout_env(self.run_dir),
        )
        return result.score if result.ok else None


def run_search(cfg: RunConfig, tui: bool = False,
               proposer_fn: ProposerFn = propose,
               status_fn: Callable[[str], None] = print) -> None:
    """Drive the configured strategy to completion (resume-safe, Ctrl-C-safe)."""
    engine = Engine(cfg, proposer_fn=proposer_fn, status_fn=status_fn)

    if tui:
        from braided.tui import TUI

        with TUI(engine) as t:
            engine.status_fn = t.status
            try:
                _drive(engine, cfg, t.status)
            finally:
                engine.status_fn = status_fn
        return

    _drive(engine, cfg, status_fn)


def _drive(engine: Engine, cfg: RunConfig, status_fn: Callable[[str], None]) -> None:
    if cfg.search.strategy == "greedy":
        from braided.scheduler.greedy import GreedyStrategy

        strategy = GreedyStrategy(engine)
    elif cfg.search.strategy == "tree":
        from braided.scheduler.tree import TreeStrategy

        strategy = TreeStrategy(engine)
    else:
        raise NotImplementedError(f"strategy {cfg.search.strategy!r} lands in a later phase")

    done = engine.attempts_done()
    if done:
        status_fn(f"resuming: {done}/{cfg.search.max_total_runs} attempts already in ledger")
    try:
        while engine.attempts_done() < cfg.search.max_total_runs:
            # Don't burn the whole attempt budget against a dead/rate-limited
            # LLM: stop after 3 consecutive llm-error attempts; resume later.
            recent = engine.ledger.attempts()[-3:]
            if len(recent) == 3 and all(a.failure_kind == "llm-error" for a in recent):
                status_fn("3 consecutive LLM failures — pausing run; rerun to resume")
                return
            remaining = cfg.search.max_total_runs - engine.attempts_done()
            workers = min(cfg.search.workers, remaining)
            if workers > 1 and hasattr(strategy, "step_parallel"):
                strategy.step_parallel(workers)
            else:
                strategy.step()
    except KeyboardInterrupt:
        status_fn("interrupted — ledger and DAG are consistent; rerun to resume")
        return

    best_sha, best_score = engine.best_node()
    status_fn(f"done: {engine.attempts_done()} attempts, best {best_score:.4f} at {best_sha[:8]}")
    summary = {
        "attempts": engine.attempts_done(),
        "best_sha": best_sha,
        "best_score": best_score,
        "baseline_mean": engine.baseline["mean"],
    }
    (engine.run_dir / "result.json").write_text(json.dumps(summary, indent=2))
