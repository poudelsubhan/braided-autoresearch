"""UCB1 tree search over branches of the experiment DAG.

Each active branch is a bandit arm; pulling an arm = one propose/run/score
attempt at that branch's leaf.

Reward normalization (documented per plan): a pull's raw reward is the
attempt's improvement over its parent score (0 for rejected/failed attempts),
divided by |branch starting score| — i.e. the fractional gain relative to
where the lineage began — then clipped to [0, 1]. This keeps rewards
comparable across min/max metrics and across branches born at different
score levels. Mean reward per arm feeds standard UCB1:

    UCB(arm) = mean + c * sqrt(ln(total_pulls) / pulls(arm))

Unpulled arms are infinite (pulled first, in name order for determinism).

Lifecycle:
- Birth: `initial_branches` arms at start (main + forks from root), each with
  an LLM-generated direction hint (3.2) recorded in a `branch` fork event.
  Stagnation (last m attempts on an arm all non-accepted) forks a fresh arm
  from the current global-best node, up to `max_active_branches`.
- Death: an arm is pruned when its UCB upper bound falls below the best
  arm's lower confidence bound (never below `min_active_branches` active
  arms; the best-mean arm is never pruned).

All state is derived from ledger + graph on construction, so tree runs
resume exactly like greedy runs.
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from braided.agents.directions import generate_direction
from braided.graph import git
from braided.ledger import AttemptEvent, BranchEvent


@dataclass
class ArmStats:
    branch: str
    start_score: float
    pulls: int = 0
    rewards: list[float] = field(default_factory=list)

    @property
    def mean(self) -> float:
        return sum(self.rewards) / len(self.rewards) if self.rewards else 0.0

    def bounds(self, total_pulls: int, c: float) -> tuple[float, float]:
        """(lower, upper) confidence bounds; infinite upper if unpulled."""
        if self.pulls == 0:
            return (0.0, math.inf)
        radius = c * math.sqrt(math.log(max(total_pulls, 2)) / self.pulls)
        return (self.mean - radius, self.mean + radius)


def derive_arms(engine) -> dict[str, ArmStats]:
    """Rebuild arm statistics from ledger + graph (resume-safe, TUI-safe:
    reads only public artifacts)."""
    graph, ledger, task = engine.graph, engine.ledger, engine.task
    active: dict[str, ArmStats] = {}
    for e in ledger.events():
        if isinstance(e, BranchEvent):
            if e.action == "fork":
                start = graph.get_score(e.from_sha) if e.from_sha else None
                active[e.branch] = ArmStats(e.branch, start_score=start or 0.0)
            elif e.action == "prune":
                active.pop(e.branch, None)
    for e in ledger.events():
        if isinstance(e, AttemptEvent) and e.branch in active:
            arm = active[e.branch]
            arm.pulls += 1
            arm.rewards.append(_reward(task, graph, e, arm.start_score))
    return active


def _reward(task, graph, event: AttemptEvent, start_score: float) -> float:
    if event.result != "accepted" or event.score is None:
        return 0.0
    parent_score = graph.get_score(event.parent_sha)
    if parent_score is None:
        return 0.0
    raw = task.improvement(event.score, parent_score)
    scale = abs(start_score) or 1.0
    return max(0.0, min(1.0, raw / scale))


class TreeStrategy:
    def __init__(self, engine):
        self.engine = engine
        self.cfg = engine.cfg.search
        self._ensure_initialized()
        self.arms = derive_arms(engine)

    # -- birth ---------------------------------------------------------------

    def _ensure_initialized(self) -> None:
        engine = self.engine
        has_forks = any(
            isinstance(e, BranchEvent) and e.action == "fork"
            for e in engine.ledger.events()
        )
        if has_forks:
            return
        root = engine.graph.root()
        existing: list[str] = []
        for i in range(self.cfg.initial_branches):
            name = "main" if i == 0 else f"b{i}"
            if name != "main":
                engine.graph.create_branch(root, name)
            direction = generate_direction(engine.task, existing)
            existing.append(direction)
            engine.ledger.append(BranchEvent(
                branch=name, action="fork", from_sha=root, direction=direction,
            ))
            engine.branch_directions[name] = direction
            self.engine.status_fn(f"arm {name}: {direction[:90]}")

    def _fork_from_best(self) -> None:
        engine = self.engine
        best_sha, best_score = engine.best_node()
        n = sum(
            1 for e in engine.ledger.events()
            if isinstance(e, BranchEvent) and e.action == "fork"
        )
        name = f"b{n}"
        # merge/fork naming may collide with an existing ref; find a free one
        while name in engine.graph.branches():
            n += 1
            name = f"b{n}"
        engine.graph.create_branch(best_sha, name)
        existing = [a for a in engine.branch_directions.values()]
        direction = generate_direction(engine.task, existing)
        engine.ledger.append(BranchEvent(
            branch=name, action="fork", from_sha=best_sha, direction=direction,
            detail="stagnation fork from global best",
        ))
        engine.branch_directions[name] = direction
        self.arms[name] = ArmStats(name, start_score=best_score)
        engine.status_fn(f"fork {name} from {best_sha[:8]} ({best_score:.4f}): {direction[:70]}")

    # -- selection -----------------------------------------------------------

    def select_arm(self) -> str:
        total = max(sum(a.pulls for a in self.arms.values()), 1)
        return max(
            sorted(self.arms.values(), key=lambda a: a.branch),
            key=lambda a: a.bounds(total, self.cfg.ucb_c)[1],
        ).branch

    def select_arms(self, k: int) -> list[str]:
        total = max(sum(a.pulls for a in self.arms.values()), 1)
        ranked = sorted(
            sorted(self.arms.values(), key=lambda a: a.branch),
            key=lambda a: a.bounds(total, self.cfg.ucb_c)[1],
            reverse=True,
        )
        return [a.branch for a in ranked[:k]]

    # -- lifecycle checks ----------------------------------------------------

    def _is_stagnant(self, branch: str) -> bool:
        recent = [e for e in self.engine.ledger.attempts() if e.branch == branch]
        m = self.cfg.stagnation_m
        return len(recent) >= m and all(e.result != "accepted" for e in recent[-m:])

    def _prune_check(self) -> None:
        arms = self.arms
        pulled = [a for a in arms.values() if a.pulls > 0]
        if len(arms) <= self.cfg.min_active_branches or len(pulled) < 2:
            return
        total = max(sum(a.pulls for a in arms.values()), 1)
        best = max(pulled, key=lambda a: a.mean)
        best_lower, _ = best.bounds(total, self.cfg.ucb_c)
        victims = [
            a for a in pulled
            if a.branch != best.branch and a.bounds(total, self.cfg.ucb_c)[1] < best_lower
        ]
        victims.sort(key=lambda a: a.bounds(total, self.cfg.ucb_c)[1])
        for a in victims:
            if len(self.arms) <= self.cfg.min_active_branches:
                break
            del self.arms[a.branch]
            self.engine.ledger.append(BranchEvent(
                branch=a.branch, action="prune",
                detail=f"UCB upper {a.bounds(total, self.cfg.ucb_c)[1]:.4f} "
                       f"< best lower {best_lower:.4f}",
            ))
            self.engine.status_fn(f"prune {a.branch} (mean {a.mean:.4f}, {a.pulls} pulls)")

    def _post_pull(self, branch: str, event: AttemptEvent) -> None:
        arm = self.arms.get(branch)
        if arm is not None:
            arm.pulls += 1
            arm.rewards.append(
                _reward(self.engine.task, self.engine.graph, event, arm.start_score)
            )
        if branch in self.arms and self._is_stagnant(branch) \
                and len(self.arms) < self.cfg.max_active_branches:
            self._fork_from_best()
        self._prune_check()

    # -- stepping ------------------------------------------------------------

    def step(self) -> None:
        branch = self.select_arm()
        event = self.engine.execute_attempt(branch)
        self._post_pull(branch, event)

    def step_parallel(self, k: int) -> None:
        """Pull up to k distinct arms concurrently, each in its own worktree
        (git refuses to check one branch out in two worktrees, which is
        exactly the isolation we want). Graph/ledger writes serialize behind
        the engine lock."""
        branches = self.select_arms(k)
        if len(branches) <= 1:
            self.step()
            return
        engine = self.engine
        wt_root = engine.run_dir / "worktrees"
        wt_root.mkdir(exist_ok=True)
        current = engine.graph.current_branch()
        worktrees = {}
        with engine.lock:
            indices = {}
            base_idx = engine.ledger.next_attempt_index()
            for i, b in enumerate(branches):
                indices[b] = base_idx + i
                if b != current:
                    path = wt_root / b
                    git(engine.repo, "worktree", "add", "-f", str(path), b)
                    worktrees[b] = path
        try:
            with ThreadPoolExecutor(max_workers=len(branches)) as pool:
                futures = {
                    b: pool.submit(
                        engine.execute_attempt, b,
                        worktree=worktrees.get(b), idx=indices[b],
                    )
                    for b in branches
                }
                events = {b: f.result() for b, f in futures.items()}
        finally:
            for b, path in worktrees.items():
                git(engine.repo, "worktree", "remove", "--force", str(path), check=False)
        for b in branches:
            self._post_pull(b, events[b])
