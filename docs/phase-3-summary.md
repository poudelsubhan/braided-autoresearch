# Phase 3 summary — tree search over the DAG

## What was built

- **UCB scheduler (3.1)**: `braided/scheduler/tree.py`. Arms = active branches; a pull = one
  propose/run/score attempt at the branch leaf. **Reward normalization**: raw reward =
  improvement over the attempt's parent (0 if not accepted), divided by |branch starting
  score|, clipped to [0,1] — fractional gain relative to where the lineage began, comparable
  across min/max metrics and birth points. UCB1 `mean + c·sqrt(ln N / n)` with configurable
  `ucb_c`; unpulled arms have infinite upper bound and are pulled first in name order
  (deterministic). **Birth**: `initial_branches` arms at start; stagnation (last `stagnation_m`
  attempts on an arm all non-accepted) forks a new arm from the current global-best node,
  capped by `max_active_branches`. **Death**: prune when an arm's upper bound < best arm's
  lower bound, floored at `min_active_branches`; the best-mean arm is never pruned.
  All arm state is derived from ledger + graph on construction → tree runs resume exactly like
  greedy runs.
- **Proposer diversification (3.2)**: `braided/agents/directions.py` — at branch birth an LLM
  generates a one-sentence exploration mandate disjoint from existing lineages' (static
  fallback pool if the LLM is unavailable); persisted in the `branch` fork event, injected
  into every proposal on that branch. Exists to protect Phase 4: replication only means
  something if lineages are genuinely different-ish.
- **Parallel pulls (3.3)**: `step_parallel(k)` pulls k distinct arms concurrently, each in its
  own `git worktree` (git itself refuses to double-checkout a branch — exactly the isolation
  needed). Attempt indices pre-allocated and graph/ledger writes serialized behind an engine
  RLock. Off by default (`workers: 1`); for CPU-bound tasks only.
- **Comparison harness (3.4)**: `braided/report/compare.py` — best-public-score-vs-attempts
  table + matplotlib curves for ≥2 runs of the same task. The Phase 5 bake-off instrument.
- **Live TUI (3.5)**: `braided/tui.py`, `braided run --tui`. Rich Live two-panel layout:
  left = decorated DAG (from 1.4) + per-arm UCB stats (pulls, mean reward, upper bound);
  right = streaming attempt log with fork/prune/merge highlights. Reads only ledger + graph
  (Forge pattern) so `--tui` cannot perturb the run.

## Live Run 3A (tree on cpu-optimize, 30 attempts, 3 lineages)

- Baseline mean **15.02** items/sec (std 0.138 → threshold 0.207).
- Direction hints drawn by the LLM at birth: main = data-layout/memory-access restructuring;
  b1 = algorithmic redundancy elimination; b2 = parallelism/concurrency.
- Result: best public score **3272.20** at `524f2562` (20 accepted /
  10 rejected / 0 failed across 3 lineages; 3 (initial) forks,
  0 prunes).
- ≥2 lineages accepted commits: b1: 10, main: 8, b2: 2 — all three lineages accepted commits.
- Held-out: baseline **40.60** → best **4831.53**.
- TUI verified separately on a live run (headless render also unit-tested); the run itself was
  executed with plain status streaming because it ran unattended in the background. UCB pull
  decisions are visible in the ledger ordering and the per-arm stats.
- Operational note: the run rode through sustained LLM rate-limit throttling (retry with
  15/60s backoff per call; self-pause after 3 consecutive LLM failures, idempotent resume).

## Acceptance evidence

- Unit tests (`tests/test_tree.py`): UCB bounds against hand-computed values (means, radii,
  infinite-for-unpulled); reward normalization to 0.2 for a +2-on-10 accepted change;
  stagnation forks originate from the global-best node; prune fires only when UCB intervals
  separate and respects the floor; resume rebuilds arms without duplicate forks; two parallel
  workers in separate worktrees produce a consistent DAG (verify-ledger clean, contiguous
  indices, both arms pulled, worktrees cleaned up).
- `braided verify-ledger --run runs/run3a-tree-cpu`: consistent.
