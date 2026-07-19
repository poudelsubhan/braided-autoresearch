# Braided Autoresearch

**An autonomous AI research agent that optimizes code by searching over a git DAG — combining bandit-driven tree search, LLM-mediated semantic merges, and cross-lineage replication as a reward-hacking detector.**

Most LLM-driven code-optimization loops (e.g. Karpathy-style "keep-or-revert") are **linear**: propose a change, score it, keep it if it helps, revert if it doesn't. That design throws away two things a human researcher relies on — the ability to explore **multiple hypotheses in parallel**, and the ability to **combine** independently-discovered improvements. Braided Autoresearch replaces the linear loop with a search over a real git commit graph:

- **Tree search** — a UCB1 multi-armed bandit schedules which experiment lineage (git branch) to extend next, balancing exploration of new ideas against exploitation of what's working.
- **Composition via semantic merges** — a merge agent (an LLM) rewrites two lineages' changes into one combined patch against their common ancestor, producing a *true two-parent git merge commit*. This is a semantic merge, not git's textual merge.
- **Self-consistency as a reward-hacking check** — an LLM classifier tags each accepted change with a change-class; when the same class of improvement is independently rediscovered on separate lineages, it's flagged as *replicated*. The hypothesis: replicated improvements generalize better to a held-out (private) scorer, so replication acts as a detector for metric gaming / overfitting to the public benchmark.

Every experiment is a commit, every hypothesis lineage is a branch, every composition is a merge commit, and scores live in `git notes` — so the entire search history is inspectable with ordinary git tooling.

## Results: three strategies, same budget, same task

Bake-off on the `cpu-optimize` benchmark (a deliberately naive tokenizer + n-gram counter; score = throughput with a byte-exact correctness oracle), 30 scored attempts per strategy, identical noise-calibrated acceptance rule:

| strategy | description | best score | gain over baseline |
|---|---|---:|---:|
| **braided** | tree search + semantic merges + replication tagging | **5826.5** | **+38219%** |
| greedy | single-lineage keep-or-revert (control) | 4806.5 | +31862% |
| tree | UCB1 branching, no merges | 3272.2 | +21679% |

![bake-off curves](figures/bakeoff.png)

The braided run's winning lineage is a merge: one branch found algorithmic wins (dict counting, heap top-k), another found parallelism wins (chunking, `imap_unordered`), and the merge agent composed them.

**Honest negative result:** on this task, replicated changes did *not* retain more held-out gain than unreplicated ones — because the public scorer's byte-exact correctness oracle blocks reward hacking by construction, so there was nothing for the detector to catch (both groups retained ≥100% of their gain). The replication signal targets tasks with softer scorers (e.g. validation loss), where public gains *can* be fake. Full analysis, interaction map of which change-classes compose vs. interfere, and rendered DAGs: [REPORT.md](REPORT.md).

## How it works

```
propose ──► apply patch ──► sandboxed scorer ──► accept / reject
   ▲              (git commit + git note)             │
   │                                                  ▼
UCB1 bandit ◄── ledger.jsonl ◄── replication tagger ◄─┘
   │
   └──► every N attempts: merge agent composes two lineages
```

1. **Proposer agent** sees the task, current code, its lineage's rationale trail, its own recent failures (so it stops repeating dead ends), and one-line digests of sibling branches. It returns one focused change: `{rationale, unified diff}`.
2. **Sandbox runner** applies the patch in the experiment repo, enforces protected paths (the scorer is off-limits to the inner loop), runs the scorer in a subprocess with a timeout, and parses `{"score": float}` — or records a structured failure (`timeout | crash | invalid-output | protected-path-violation`).
3. **Noise-calibrated acceptance**: a change is kept only if it beats its parent by more than 1.5× the run-to-run standard deviation measured at init. Accepted → commit + score note; rejected → revert.
4. **Scheduler** (greedy / UCB1 tree / braided) picks the next branch to extend; the braided strategy additionally triggers the **merge agent** on a configurable cadence.
5. **Replication tagger** classifies accepted changes into change-classes and marks cross-lineage rediscoveries (`◆` in the tree view).
6. A held-out **private scorer** (different, hidden input distribution) is never shown to the loop — it's used post-hoc to measure how much of each public gain was real.

Two git repos, never confused: this **harness repo** holds the codebase; each run gets its own **experiment repo** under `runs/<run-id>/repo/` where all branch/commit/merge operations happen.

## Quickstart

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/), git, and an `ANTHROPIC_API_KEY` (agents use the Claude API).

```sh
uv sync

# create a fresh experiment repo, measure baseline score + noise floor
uv run braided init-task cpu-optimize --strategy braided --merge-cadence 10 --attempts 30

# drive the search loop (resumable; --tui for a live dashboard)
uv run braided run --config runs/<run-id>/run.yaml

# inspect the decorated commit DAG: scores, rationales, merges, ◆ replicated classes
uv run braided report --tree --run runs/<run-id>

# cross-check the append-only ledger against the git DAG
uv run braided verify-ledger --run runs/<run-id>

# post-hoc held-out scoring + final report figures
uv run braided heldout-sweep --run runs/<run-id>
uv run braided report --final --runs runs/<run-a> runs/<run-b> ...
```

## Repository layout

| path | what it is |
|---|---|
| `braided/graph.py` | git-DAG wrapper: branches, commits, true two-parent merge commits, scores in `git notes` |
| `braided/ledger.py` | append-only `ledger.jsonl` event log, cross-checkable against git |
| `braided/scheduler/` | search strategies: `greedy`, `tree` (UCB1), `braided` |
| `braided/agents/` | proposer, merge agent, replication classifier (Anthropic API) |
| `braided/runner.py` | sandboxed scorer execution with timeout + failure taxonomy |
| `braided/accept.py` | noise-calibrated acceptance threshold |
| `braided/context.py` | agent context builders: rationale trails, failure digests, sibling digests |
| `braided/report/` | decorated tree view, figures, final report, ledger audit |
| `tasks/cpu-optimize/` | CPU benchmark task: naive tokenizer + top-k n-gram counter, correctness-gated throughput scorer |
| `tasks/nanogpt/` | GPU task adapter: char-level nanoGPT, fixed-wall-clock validation-loss scorer |
| `runs/` | experiment repos + ledgers created at runtime (gitignored) |
| `docs/` | per-phase build summaries, demo script, slides |
| `REPORT.md` | full experimental report: bake-off, replication analysis, interaction map, DAGs |

## Adding a task

A task is a directory with a `task.yaml` (entry command, public scorer command, private held-out scorer command, protected paths, wall-clock budget) plus a `template/` seeded into a fresh experiment repo. The scorer must print `{"score": <float>}` on stdout. Protected paths keep the scorer un-editable by the agent — enforced by path checks before any patch is applied.

## Limitations

- The acceptance threshold is calibrated once at init; scorer variance drifts with machine load, so near-threshold decisions are noisy.
- Lineage independence is approximate (one proposer model, siblings see each other's digests) — replication is evidence, not proof, of independent rediscovery.
- Results are from one task family at hackathon sample size; see [REPORT.md](REPORT.md) §5 for the full list.

## Roadmap

- **Extrapolator**: when a change-class replicates, switch that branch's proposer from "one focused change" to "push this mechanism to its endpoint".
- **Sandbox hardening**: E2B/Modal instead of subprocess isolation.
- **GPU series**: run the nanogpt task, where a soft scorer (val loss) gives the replication detector something real to catch.

---

*Topics: LLM agents · autonomous research · agentic search · AI for code optimization · tree search · UCB1 bandit · git DAG · semantic merge · reward hacking · self-consistency · evolutionary program search · AutoML · Claude API*
