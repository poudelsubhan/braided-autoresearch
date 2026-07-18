# braided-autoresearch

Replace the linear keep-or-revert autoresearch loop with a git-DAG search:

- **Tree search** — bandit-scheduled branching over an experiment git repo (UCB over lineages).
- **Composition** — agent-mediated semantic merges (a merge agent rewrites a combined patch against the common ancestor; true two-parent merge commits).
- **Self-consistency** — cross-lineage replication tagging as a reward-hacking detector: improvements that replicate across independent lineages are expected to generalize better to a held-out scorer.

## Layout

- `braided/` — the harness (graph, ledger, scheduler, agents, runner, tasks, report).
- `tasks/<name>/` — benchmark task adapters (`task.yaml` + `template/` seeded into a fresh experiment repo).
- `runs/<run-id>/` — experiment repos + ledgers created at runtime (gitignored).

Two git repos, never confused: this **harness repo** holds the codebase; each run gets its own **experiment repo** under `runs/<run-id>/repo/` where all branch/commit/merge graph operations happen.

## Quickstart

```sh
uv sync
uv run braided init-task cpu-optimize            # fresh experiment repo + baseline score
uv run braided run --config runs/<run-id>/run.yaml
uv run braided report --tree --run runs/<run-id>
```

See `BRAIDED_AUTORESEARCH_PLAN.md` for the phase-gated build plan and `docs/phase-N-summary.md` for what was built per phase.
