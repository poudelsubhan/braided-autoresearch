# Demo runbook (~3 minutes)

## The one-sentence pitch

Karpathy-style autoresearch is a straight line — propose, keep or revert, repeat. We turned
it into a **git DAG**: a bandit decides which research lineage to grow, an agent writes
semantic merges between lineages as true two-parent merge commits, and when independent
lineages rediscover the same mechanism we tag it **replicated** — which turns out to predict
that the improvement survives a hidden held-out scorer. Self-consistency as a free
reward-hacking detector.

## Before you present (5 min prep)

1. Start a live run in one terminal (any strategy; braided is prettiest):
   `uv run braided run --config runs/<demo-run>/run.yaml --tui`
   (Fresh dir: `uv run braided init-task cpu-optimize --run-id demo && edit runs/demo/run.yaml`
   to set `strategy: braided`, `merge_cadence: 5`.)
2. Serve the live page in another: `cd runs/<demo-run> && python -m http.server 8123`,
   open http://localhost:8123/graph.html in the browser you'll project.
3. Have `REPORT.md` (post-bake-off) or `runs/run2a-greedy-cpu/heldout.json` open as backup.

## The 3-minute flow

1. **The task (20s).** Show `tasks/cpu-optimize/template/pipeline.py` — deliberately naive
   n-gram counter. "The loop must speed this up without changing observable behavior; a
   protected scorer correctness-checks every candidate against a reference implementation."

2. **The loop is real (40s).** Terminal with the live TUI: DAG on the left growing branch by
   branch with UCB stats per arm, attempt log streaming on the right. Point at one rationale:
   the model states a mechanism, patches the code, a sandboxed scorer decides. Rejections and
   crashes are logged, not fatal.

3. **The headline numbers (30s).** Run 2A: baseline **15 → 4,312 items/sec in 20 attempts**
   (287×) — and on a *hidden* input distribution the same code goes **40 → 6,916** (172×),
   so it's real optimization, not scorer-gaming. All of it in ordinary git: every node is a
   commit, scores ride in `git notes`, `braided verify-ledger` cross-checks the ledger
   against the DAG.

4. **The novel bits (60s).** Browser with `graph.html` (or `braided report --tree`):
   - branches = independent research lineages with distinct LLM-drawn directions;
   - a diamond/blue node = an **agent-written semantic merge** — a true two-parent git merge
     commit whose tree the merge agent rewrote against the common ancestor;
   - `◆` = a change-class that **replicated** in ≥2 independent lineages (independence is a
     pure DAG computation — no LLM in the verdict).

5. **The finding (30s).** REPORT.md figures: bake-off curves (same budget, three
   strategies), and the replication-vs-generalization scatter — replicated improvements
   retain more of their gain on the held-out scorer. Directional at hackathon n, but the
   detector costs nothing: it falls out of running research as a DAG instead of a line.

## Fallbacks

- **Rate-limited / no network:** skip the live run; `uv run braided report --tree --run
  runs/run3a-tree-cpu` renders the finished DAG instantly, and `graph.html` works on the
  static `graph.json` already in the run dir.
- **TUI misbehaves on the projector:** plain `braided run` prints one status line per
  attempt; the browser page carries the visuals.
- **Questions on cheating:** show `score_common.py` (protected, correctness assert), the
  `protected-path-violation` failure kind in the ledger, and `braided/privacy.py` (held-out
  command can never appear in agent context).
