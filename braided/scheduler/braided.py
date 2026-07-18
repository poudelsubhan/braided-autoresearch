"""Braided strategy = tree search + merge daemon + classifier/verifier sweeps.

Every `merge_cadence` attempts: (1) classify newly accepted attempts,
(2) recompute replication tags, (3) try one semantic merge. Merge attempts
consume attempt budget (charged like any pull); classification/verification
are bookkeeping, not attempts.
"""

from __future__ import annotations

from braided.agents.classifier import classify_new_accepted
from braided.agents.llm import LLMError
from braided.agents.merger import pick_merge_pair, propose_merge
from braided.agents.proposer import ProposalError
from braided.graph import PatchError, ProtectedPathViolation
from braided.ledger import MergeAttemptEvent
from braided.runner import run_scorer
from braided.scheduler.tree import TreeStrategy
from braided.verify import check_replication


class BraidedStrategy(TreeStrategy):
    def __init__(self, engine):
        super().__init__(engine)
        self._merge_seq = 0  # merge-branch naming

    def step(self) -> None:
        cadence = self.cfg.merge_cadence
        done = self.engine.attempts_done()
        if cadence > 0 and done > 0 and done % cadence == 0 \
                and not self._merged_at(done):
            self._housekeeping()
            self._try_merge()
            return
        super().step()

    def _merged_at(self, done: int) -> bool:
        """Resume guard: was a merge already attempted at this attempt index?"""
        return any(e.attempt_index == done for e in self.engine.ledger.merge_attempts())

    def _housekeeping(self) -> None:
        engine = self.engine
        try:
            assigns = classify_new_accepted(engine.run_dir)
        except Exception as e:  # classification must never kill the run
            engine.status_fn(f"classifier sweep failed: {e}")
            assigns = []
        for a in assigns:
            engine.status_fn(f"class {a.sha[:8]} -> {a.class_id}")
        tags = check_replication(engine.run_dir, engine.cfg.search.replication_k)
        for t in tags:
            engine.status_fn(
                f"◆ replicated: {t.class_id} in {len(t.lineages)} lineages "
                f"({', '.join(t.lineages)})"
            )

    def _try_merge(self) -> None:
        engine = self.engine
        idx = engine.ledger.next_attempt_index()
        plan = pick_merge_pair(engine.graph, engine.task, min_score_gain=0.0)
        if plan is None:
            engine.status_fn(f"[{idx:03d}] merge: no eligible pair, pulling arm instead")
            super().step()
            return

        score_a = engine.graph.get_score(plan.sha_a)
        score_b = engine.graph.get_score(plan.sha_b)
        event = MergeAttemptEvent(
            attempt_index=idx,
            parents=[plan.sha_a, plan.sha_b],
            base_sha=plan.base_sha,
            result="fail",
            parent_scores=[score_a, score_b],
        )

        try:
            proposal = propose_merge(engine.graph, engine.task, plan)
        except (ProposalError, LLMError) as e:
            event.failure_kind = "bad-proposal"
            event.detail = str(e)[:500]
            return self._finish_merge(event, plan)

        event.rationale = proposal.rationale
        (engine.run_dir / "logs" / f"attempt-{idx:04d}.patch").write_text(proposal.patch)

        # dry-run the combined patch on the ancestor in the main worktree
        try:
            engine.graph.checkout(plan.base_sha)
            engine.graph.apply_patch(proposal.patch, protected=engine.task.protected_paths)
        except ProtectedPathViolation as e:
            event.failure_kind = "protected-path-violation"
            event.detail = str(e)[:500]
            return self._finish_merge(event, plan)
        except PatchError as e:
            event.failure_kind = "patch-apply-failed"
            event.detail = str(e)[:500]
            return self._finish_merge(event, plan)

        result = run_scorer(
            engine.repo, engine.task, engine.run_dir / "logs", log_prefix=f"attempt-{idx:04d}"
        )
        if not result.ok:
            event.failure_kind = result.failure_kind
            event.detail = result.detail[:500]
            return self._finish_merge(event, plan)

        event.score = result.score
        beats_a = engine.task.better(result.score, score_a) if score_a is not None else True
        beats_b = engine.task.better(result.score, score_b) if score_b is not None else True
        if not (beats_a and beats_b):
            event.result = "interfere"
            return self._finish_merge(event, plan)

        # compose: true two-parent merge node on a fresh branch = new active arm
        while f"m{self._merge_seq}" in engine.graph.branches():
            self._merge_seq += 1
        branch = f"m{self._merge_seq}"
        with engine.lock:
            sha = engine.graph.merge_commit(
                plan.sha_a, plan.sha_b, proposal.patch,
                f"[{idx:04d}] merge {plan.branch_a}+{plan.branch_b}: {proposal.rationale[:60]}",
                branch=branch, protected=engine.task.protected_paths,
                score=result.score, rationale=proposal.rationale,
            )
        event.result = "compose"
        event.sha = sha
        event.branch = branch
        from braided.ledger import BranchEvent
        from braided.scheduler.tree import ArmStats

        engine.ledger.append(BranchEvent(
            branch=branch, action="fork", from_sha=sha,
            direction=f"This lineage extends the composed merge of {plan.branch_a} and {plan.branch_b}.",
            detail="merge compose",
        ))
        engine.branch_directions[branch] = (
            f"This lineage extends the composed merge of {plan.branch_a} and {plan.branch_b}."
        )
        self.arms[branch] = ArmStats(branch, start_score=result.score)
        return self._finish_merge(event, plan)

    def _finish_merge(self, event: MergeAttemptEvent, plan) -> None:
        engine = self.engine
        with engine.lock:
            engine.ledger.append(event)
            engine.refresh_live_artifacts()
        # always leave the tree on a real branch
        engine.graph.checkout(event.branch or plan.branch_a)
        scores = ", ".join(
            f"{s:.4f}" if s is not None else "?" for s in event.parent_scores
        )
        score_s = f"{event.score:.4f}" if event.score is not None else "-"
        engine.status_fn(
            f"[{event.attempt_index:03d}] merge {plan.branch_a}+{plan.branch_b} "
            f"{event.result:<10} score={score_s} parents=[{scores}] "
            f"disjoint={plan.disjointness:.2f}"
            + (f" -> new arm {event.branch}" if event.result == "compose" else "")
        )
