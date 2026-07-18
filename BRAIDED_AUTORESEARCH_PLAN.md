# Braided Autoresearch — Phase-Gated Build Plan

**Target agent:** Claude Code
**Repo name:** `braided-autoresearch`
**One-line thesis:** Replace the linear keep-or-revert autoresearch loop with a git-DAG search: bandit-scheduled branching (tree search), agent-mediated semantic merges (composition), and cross-lineage replication tagging (self-consistency as a reward-hacking detector).

---

## Global rules for the agent (read before Phase 0)

1. **Phase gates are hard.** Do not begin any task in Phase N+1 until every task in Phase N is complete, tested, explained, and committed. No exceptions, no "I'll come back to it."
2. **Gate ritual.** At the end of each phase, in order:
   a. **Explain** — write `docs/phase-N-summary.md`: what was built, key design decisions, deviations from this plan and why, and how the components connect to the graph model (nodes = commits+scores, edges = diffs+rationales, ledger = sidecar).
   b. **Test** — run the full test suite (`pytest -x`). All tests green. Phase-specific acceptance checks (listed per phase) must pass.
   c. **Commit** — one commit per completed task during the phase is fine, but the phase closes with a tagged commit: `git tag phase-N-complete`.
3. **Parallelism.** Tasks within a phase are designed to be independent. Execute them in parallel where your tooling allows (subagents / parallel tool calls); otherwise any order works — they do not depend on each other unless explicitly noted.
4. **Two git repos, never confuse them.** The **harness repo** is this codebase. The **experiment repo** is a separate git repo the loop operates on (one per task, created at runtime under `runs/<run-id>/repo/`). All graph operations (branch/commit/merge) happen in the experiment repo. The harness repo only ever receives your development commits.
5. **The scorer is off-limits to the inner loop.** Task scorer files are listed in each task's `protected` config. The proposer must never be allowed to edit them. Enforce with a path check before applying any patch.
6. **Determinism where possible.** Fixed seeds for scorers, fixed wall-clock budget per experiment run, identical budget across all search strategies. Comparability is the entire experimental design.
7. **Language/stack:** Python 3.11+, `pytest`, `GitPython` (or subprocess git — your call, justify in the phase summary), `pydantic` for schemas, Anthropic API for the proposer/merge/verifier agents. Keep it minimal — Karpathy's reference is 630 lines; do not build a framework.

---

## Phase 0 — Scaffold and task substrate

**Goal:** repo skeleton, config system, and two runnable benchmark tasks behind one adapter interface.

### Tasks (parallel)

- **0.1 Repo scaffold.** Package layout `braided/` (modules: `graph`, `ledger`, `scheduler`, `agents`, `runner`, `tasks`, `report`), `pyproject.toml`, `pytest` config, `README.md` stub, `.gitignore` (ignore `runs/`), CLI entrypoint `braided/cli.py` with subcommands stubbed: `init-task`, `run`, `report`.
- **0.2 Config schema.** Pydantic models in `braided/config.py`: `TaskConfig` (name, entry command, scorer command, protected paths, run budget seconds, public metric name, private/held-out scorer command), `SearchConfig` (strategy: `greedy | tree | braided`, max total runs, UCB exploration constant, merge cadence, replication threshold k), `RunConfig` (composition of the two + seed + output dir). Load from a single `run.yaml`.
- **0.3 Task adapter: `nanogpt-shakespeare` (GPU path).** Vendor a minimal char-level nanoGPT trainer (~300 lines, single file `train.py`) into `tasks/nanogpt/template/`. Scorer: `score.py` runs training with a **fixed wall-clock budget (default 180s)** and prints final validation loss as JSON `{"score": <float>}` (lower is better). Held-out scorer: same model checkpoint evaluated on a held-out split of the text. `train.py` is editable by the loop; `score.py` and the data-split logic are protected. Download tinyshakespeare at init time.
- **0.4 Task adapter: `cpu-optimize` (no-GPU fallback).** A deliberately naive pure-Python data-processing function (e.g., a tokenizer + top-k n-gram counter over a ~10MB text corpus, written inefficiently: repeated string concat, no dicts where dicts belong, quadratic scans). Scorer: `score.py` benchmarks throughput on a **fixed public input set** and prints `{"score": <items_per_sec>}` (higher is better) after asserting output correctness against golden outputs. Held-out scorer: same benchmark on a **different, hidden input distribution**. Correctness assertion is the anti-cheat: a patch that breaks outputs scores zero.
- **0.5 Sandbox runner.** `braided/runner.py`: given an experiment-repo working tree and a `TaskConfig`, execute the scorer command in a subprocess with (a) wall-clock timeout = budget + 30s grace, (b) working directory isolation, (c) captured stdout/stderr persisted to the ledger, (d) parsed JSON score or a structured failure (`timeout | crash | invalid-output | protected-path-violation`). No Docker required for v1; process isolation + path checks suffice for a hackathon. Note in the summary that E2B/Modal is the production upgrade.

### Acceptance
- `braided init-task nanogpt-shakespeare` and `braided init-task cpu-optimize` each create a fresh experiment repo with an initial commit and a baseline score recorded.
- Running the scorer twice on the unmodified baseline produces scores within noise tolerance (document the observed variance — you will need it to set the accept threshold).
- Unit tests: config round-trip, protected-path enforcement, runner failure taxonomy.

**GATE: explain → test → commit → `git tag phase-0-complete`.**

---

## Phase 1 — Graph substrate and ledger

**Goal:** the experiment repo becomes a decorated DAG: git for topology, sidecar ledger for scores/rationales/failures.

### Tasks (parallel)

- **1.1 Graph API.** `braided/graph.py`: wrapper over the experiment repo exposing `nodes()` (commit SHA, parent SHAs, branch, score), `leaves()`, `create_branch(from_sha)`, `commit_patch(branch, patch, message)`, `merge_commit(sha_a, sha_b, patch, message)` (creates a true two-parent merge commit whose tree is the agent-written combined patch, **not** git's textual merge), and `lineage(sha)` (path back to root). Scores read/written via `git notes` namespace `refs/notes/score`.
- **1.2 Ledger.** `braided/ledger.py`: append-only `ledger.jsonl` in the run dir. Event types (pydantic): `attempt` (branch, parent_sha, rationale, diff_summary, result: accepted/rejected/failed, score, failure_kind), `merge_attempt` (parents, rationale, result, score_vs_parents), `replication_tag` (class_id, class_summary, member_shas, lineages). Every event timestamped and seeded with the run id. The ledger must be reconstructible-from and cross-checkable-against git (`braided verify-ledger` command that walks the DAG and confirms every accepted node has an attempt event and a score note).
- **1.3 Graph queries for agent context.** `braided/context.py`: functions producing the strings agents will see: `rationale_trail(branch, n)` (last n accepted rationales on this lineage), `sibling_digest(branch)` (one line per other active branch: current score + last rationale), `failure_digest(branch, n)` (recent failed/rejected attempts on this lineage — so the proposer stops repeating dead ends), `replicated_classes_digest()` (stub until Phase 4; returns empty).
- **1.4 Terminal graph view (decorated git log).** `braided report --tree`: shell out to `git log --graph --oneline --all` in the experiment repo, then rewrite each commit line to append its score (from `git notes`) and the first ~50 chars of its rationale. Git renders the topology (branches, merge diamonds) natively; you only decorate. ANSI color: green for accepted nodes, cyan for merge commits, dim for pruned branches, and a `◆` marker for replicated-class members (stub until Phase 4). ~10–20 lines of real logic. This is the day-to-day inspection tool for every live run.

### Acceptance
- Scripted test: build a synthetic 3-branch DAG with one merge via the Graph API only; `verify-ledger` passes; `braided report --tree` renders the topology with scores and rationales visible; `lineage()` and `leaves()` return correct sets.
- Ledger survives process restart (re-open and append).

**GATE: explain → test → commit → `git tag phase-1-complete`.**

---

## Phase 2 — The inner loop (greedy baseline, first live run)

**Goal:** a working single-lineage Karpathy-style loop end to end. This is the control condition for the final experiment and your first real autoresearch run.

### Tasks (parallel)

- **2.1 Proposer agent.** `braided/agents/proposer.py`: given task description + current code + `rationale_trail` + `failure_digest` (+ `sibling_digest` and `replicated_classes_digest`, empty for now), produce a JSON response: `{rationale: str, patch: unified-diff}`. System prompt requirements: one focused change per proposal; rationale must state the mechanism ("widen MLP 4x→8x because the model is underfitting at this budget"), not restate the diff; never touch protected paths; prefer changes that improve score *within the fixed budget* (efficiency, not scale). Patch application via `git apply` with rejection handling — a malformed patch is a `failed` attempt, logged, not fatal.
- **2.2 Greedy strategy.** `braided/scheduler/greedy.py`: single branch `main`. Loop: propose → apply → run → score → accept if score improves on parent beyond the noise tolerance measured in Phase 0, else revert working tree. Log every attempt. Stop at `max_total_runs`.
- **2.3 Run orchestration + resume.** `braided/cli.py run --config run.yaml`: drives any strategy, streams a one-line status per attempt (attempt #, branch, accepted/rejected, score, best-so-far), handles Ctrl-C cleanly, and can **resume** an interrupted run from the graph + ledger state (idempotent restart — critical for overnight runs).
- **2.4 Noise-calibrated acceptance.** Small module computing the accept threshold from Phase 0's variance measurement (e.g., accept only if improvement > 1.5× observed run-to-run std of the baseline). Configurable. Document the tradeoff in code comments: too loose → accepting noise, ratchet fills with junk; too tight → real small wins rejected.

### Acceptance
- **LIVE RUN 2A (you, the human, run this):** `braided run --config runs/greedy-cpu.yaml` — greedy loop on `cpu-optimize`, 20 attempts. Expect visible throughput gains (the seed function is deliberately bad; the loop should find dict lookups, join instead of concat, etc.). Deliverable: ledger + graph SVG + best-vs-baseline public and held-out scores.
- **LIVE RUN 2B (GPU available):** same on `nanogpt-shakespeare`, 15–20 attempts at 180s each (~1 hour). If no GPU, skip and note it.
- Unit tests: patch rejection path, resume-from-interrupt, accept-threshold logic.

**GATE: explain → test → commit → `git tag phase-2-complete`.**

---

## Phase 3 — Tree search over the DAG

**Goal:** the graph grows in more than one direction; a bandit decides where.

### Tasks (parallel)

- **3.1 UCB scheduler.** `braided/scheduler/tree.py`: maintain active branches (start 3, configurable). Each "arm" is a branch; pulling an arm = one propose/run/score attempt at that branch's leaf. Reward signal for UCB: normalized improvement over the branch's starting score (define precisely in code; document the normalization). Standard UCB1 with configurable exploration constant. Branch birth: when a branch's last m attempts all rejected, fork a new branch from the current global-best node (stagnation → diversify). Branch death: prune arms whose upper confidence bound falls below the global best's lower bound (with a floor of `min_active_branches`).
- **3.2 Proposer diversification.** Extend the proposer so each branch carries a persistent `direction` hint generated at branch birth ("this lineage explores optimizer/schedule changes"; "this lineage explores architecture") — stored in the ledger, injected into that branch's proposals. Without this, all lineages converge on the same obvious edits and Phase 4's replication signal is worthless. This task exists to protect the Phase 4 experiment: replication only means something if lineages are genuinely independent-ish.
- **3.3 Parallel pulls (optional but cheap).** If the task is CPU-bound (`cpu-optimize`), allow n workers pulling different arms concurrently, each in its own working-tree checkout (`git worktree`). Serialize graph commits behind a lock. For GPU tasks default to serial. Config flag.
- **3.4 Strategy-comparison harness.** `braided/report/compare.py`: given ≥2 completed run dirs, produce a comparison table + matplotlib plot: best public score vs attempts consumed, per strategy, same task, same total budget. This is the bake-off instrument for Phase 5.
- **3.5 Live TUI.** `braided/tui.py`, activated with `braided run --tui`: a Rich `Live` two-panel layout. Left: the decorated DAG from 1.4, refreshed after every attempt, with per-branch UCB stats (pulls, mean reward, upper bound) in a footer row per arm. Right: streaming attempt log — one line per attempt with branch, accepted/rejected/failed, score, best-so-far, and merge/fork events highlighted. The purpose is operational: watching arm-pull decisions live is how you debug a starving or runaway bandit in Phase 3–4. Reuse the Forge pattern: TUI reads only from the ledger and graph (no private state), so `--tui` off changes nothing about the run.

### Acceptance
- **LIVE RUN 3A:** tree strategy on `cpu-optimize`, 30 attempts, 3 lineages, run with `--tui`. Verify from the tree view that ≥2 lineages accepted commits and the ledger shows UCB pull decisions; confirm the TUI panels update live without perturbing the run.
- Unit tests: UCB arithmetic against hand-computed values; stagnation-fork; prune logic; worktree isolation (two workers can't corrupt the DAG).

**GATE: explain → test → commit → `git tag phase-3-complete`.**

---

## Phase 4 — Merge daemon and consistency verifier

**Goal:** the two novel components. Keep them separate: the verifier must never propose.

### Tasks (parallel)

- **4.1 Merge daemon.** `braided/agents/merger.py` + scheduler hook: every `merge_cadence` attempts, select the top two branches whose accepted-change sets are most disjoint (heuristic: fewest overlapping files/hunks; justify your heuristic in the summary). The merge agent receives both lineages' accepted diffs-from-common-ancestor + rationale trails and writes a **single combined patch against the common ancestor** — a rewrite, not a textual merge. Run it. Outcomes logged as `merge_attempt`: **compose** (beats both parents → commit as two-parent merge node, becomes a new active arm), **interfere** (beats neither/one → rejected, but the interference is recorded), **fail** (patch invalid/crash). The interference records accumulate into an interaction map (which change-classes stack, which fight) — surface this in the report.
- **4.2 Patch classifier.** `braided/agents/classifier.py`: LLM judge that assigns each *accepted* attempt a change-class label from an open but stable vocabulary (e.g., `lr-schedule`, `data-structure-swap`, `attention-variant`, `caching`). Prompted for consistency: it sees previously assigned class labels + exemplar diffs and must reuse a class when the mechanism matches. Persist assignments as `replication_tag` groundwork in the ledger.
- **4.3 Consistency verifier.** `braided/verify.py`: pure function over the ledger + DAG (no LLM in the decision itself, only the classifier's labels): a class is **replicated** iff accepted members appear in ≥k lineages that are independent (neither is an ancestor of the other and they don't share the class via a common-ancestor commit). Emit `replication_tag` events; update the tree view (`◆` markers) and `replicated_classes_digest()` so the proposer now receives "these directions replicate; these were tried once and never confirmed."
- **4.4 Held-out evaluation hook.** Extend the runner/report: for the final best node of each run *and* for every replicated-vs-unreplicated accepted node, run the **private held-out scorer**. Store both scores. This produces the dataset for the Phase 5 headline claim. The private scorer is never visible to any agent — enforce with the protected-path mechanism plus a config assert that the private command never appears in agent context strings.

### Acceptance
- **LIVE RUN 4A:** full braided strategy (tree + merge + verify) on `cpu-optimize`, 40–60 attempts, with `--tui`. Confirm: ≥1 merge attempted with a logged outcome; ≥1 class tagged replicated or an explicit "no class replicated" report; the tree view shows merge commits and `◆` replication markers.
- Unit tests: independence check (ancestor cases, shared-ancestor class propagation), merge-node arm registration, private-scorer leak assert.

**GATE: explain → test → commit → `git tag phase-4-complete`.**

---

## Phase 5 — The experiment and the report

**Goal:** turn the system into a finding. Same task, same total attempt budget, three strategies.

### Tasks (parallel until 5.5)

- **5.1 Bake-off runs (human-launched, agent-prepared).** Prepare three configs: `greedy`, `tree`, `braided`, identical `max_total_runs` (60 on `cpu-optimize`; if a GPU is available, a second series on `nanogpt-shakespeare` at whatever budget the clock allows — 60 × 180s ≈ 3h, feasible overnight or on 2–4 parallel GPUs via worktrees). **LIVE RUN 5A: the human runs all three.** Fixed seeds. The agent prepares configs + a `make bakeoff` target; the human presses go.
- **5.2 Replication-vs-generalization analysis.** `braided/report/replication.py`: for every accepted improvement across all runs, compare public-score gain vs held-out-score gain, split by replicated / unreplicated. The claim under test: **replicated improvements retain more of their gain on the held-out scorer than unreplicated ones** (i.e., cross-lineage replication is a free reward-hacking detector). Output: scatter + a simple effect estimate with n. Be honest about sample size — report it as a directional finding, not a p-value theater piece.
- **5.3 Interaction map.** From `merge_attempt` records: a small matrix/graph of change-class pairs → compose/interfere/unknown. Render into the report.
- **5.4 Demo graph page (OPTIONAL — only if the clock allows; skip without guilt).** A single self-contained `graph.html` (inline d3 or dagre-d3 from CDN, no build step, no server): loads a `graph.json` dump of the DAG (nodes: sha, score, branch, replicated flag; edges: parent links, merge links) plus `ledger.jsonl`, renders a left-to-right DAG layout with score-colored nodes, `◆`-flagged replicated nodes, and merge nodes as diamonds; polls the files every 3s so it grows live during a run. Add `braided report --json` to emit `graph.json`. This exists purely for projecting to judges — a research DAG growing on screen during a live run. It must read the same artifacts as everything else and touch nothing in the loop.
- **5.5 Final report (depends on 5.1–5.3).** `braided report --final`: single `REPORT.md` + figures: (1) bake-off curves, (2) the replication-vs-generalization scatter, (3) the interaction map, (4) captured `--tree` outputs for each run's final DAG, (5) limitations (noise floor, lineage independence is approximate, one task family, LLM classifier subjectivity). Write it so a track judge can read it in three minutes.

### Acceptance
- All three bake-off runs complete on `cpu-optimize`; report builds; every number in `REPORT.md` traces to a ledger entry (add a `braided report --audit` that checks each reported figure against ledger data — the writing-driven-autoresearch discipline, applied to your own report).

**GATE: explain → test → commit → `git tag phase-5-complete`. Done.**

---

## Runnable-benchmark summary (for the human)

| Run | Phase | Task | Attempts | Wall clock (est.) | Hardware |
|---|---|---|---|---|---|
| 2A | 2 | cpu-optimize, greedy | 20 | ~15–30 min | any laptop |
| 2B | 2 | nanogpt-shakespeare, greedy | 15–20 | ~1 h | 1 GPU |
| 3A | 3 | cpu-optimize, tree | 30 | ~30–45 min | any laptop |
| 4A | 4 | cpu-optimize, braided | 40–60 | ~1–1.5 h | any laptop |
| 5A | 5 | all three strategies | 3 × 60 | ~3–4 h CPU; overnight for GPU series | laptop / 1–4 GPUs |

## Priority order if the day runs short
1. Phases 0–2 (a working loop is the floor).
2. Phase 3 (tree beats greedy is already a demo). 3.5 (TUI) yields to 3.1–3.2 if forced to choose.
3. Phase 4 task 4.3 + 4.4 before 4.1 (the replication finding is the headline; merges are the flourish).
4. Phase 5.2 before 5.3. Task 5.4 (demo HTML) is the first thing cut.
