# Phase 2 summary — the inner loop (greedy baseline)

## What was built

- **Proposer agent (2.1)**: `braided/agents/proposer.py` — builds a prompt from task
  description + all editable files + `rationale_trail` + `failure_digest` + `sibling_digest` +
  `replicated_classes_digest` (+ the branch's direction hint, empty for greedy), asks for
  `{rationale, patch}` as JSON. System prompt enforces: one focused change, mechanism-stating
  rationale, no protected paths, efficiency-within-budget over scale. Lenient JSON extraction
  (fence stripping, brace matching) because models decorate. `braided/agents/llm.py` backends:
  `anthropic` SDK when `ANTHROPIC_API_KEY` is set, else headless `claude -p` (Claude Code
  login); model via `BRAIDED_MODEL`, default sonnet; retries with 15s/60s backoff.
- **Greedy strategy (2.2)**: `braided/scheduler/greedy.py` — single branch `main`,
  keep-or-revert. All real work lives in `braided/engine.py`'s `execute_attempt`: propose →
  protected-path check → apply → score → accept/commit or revert, every outcome ledgered
  (`accepted | rejected | failed:{bad-proposal, llm-error, protected-path-violation,
  patch-apply-failed, timeout, crash, invalid-output}`).
- **Orchestration + resume (2.3)**: `braided run --config run.yaml` drives any strategy;
  one flushed status line per attempt; Ctrl-C-safe; **resume is derived state** — attempt
  numbering from the ledger, code state from git, so an interrupted run restarts idempotently
  (exercised for real when the LLM rate limit killed Run 2A at attempt 10; a rerun of the same
  command finished it).
- **Noise-calibrated acceptance (2.4)**: `braided/accept.py` — threshold =
  `accept_noise_multiplier (1.5) × baseline std` from init-task calibration. Tradeoff
  documented in the module docstring (loose ⇒ ratchet fills with accepted noise, which also
  poisons the Phase 4/5 replication analysis; tight ⇒ small real wins starve).

## Deviations / additions

- **Fuzzy patch fallback (`braided/patching.py`)**: `git apply` rejects a large fraction of
  model-written diffs for a *shape* reason: a hunk whose last lines are deletions/additions
  with no trailing context gets anchored to end-of-file by git and refused, even when the
  context matches perfectly (verified by minimal repro; `--recount`/`--ignore-whitespace`
  don't help). Fallback ignores @@ numbers entirely and locates each hunk's old side by exact,
  unique content match; all-or-nothing application; ambiguity and mismatch are errors. Only
  runs after `git apply` fails; protected-path checks happen before either path. This took the
  smoke run from 3/3 patch failures to 3/3 acceptances.
- **LLM-outage guard**: after 3 consecutive `llm-error` attempts the run pauses itself
  ("rerun to resume") instead of burning the whole attempt budget against a dead API.
- **Attempt patches persisted** to `logs/attempt-NNNN.patch` for post-hoc debugging.

## Live Run 2A (greedy on cpu-optimize, 20 attempts)

- Baseline (3 calibration runs): mean **15.04** items/sec, std 0.157 → accept threshold 0.236.
- Result: best public score **4312.25** items/sec at `80e71b56` —
  **287× the baseline** in 20 attempts (8 accepted / 12 rejected
  / 0 failed).
- Held-out (hidden input distribution: different seed/shape/vocab, 3-grams vs 2-grams):
  baseline **40.13** → best **6916.13** items/sec (**172×**).
- Accepted mechanisms, in ledger order: dict counting instead of list.index scans (49×); stopword frozenset hoisted out of the loop; regex tokenization instead of per-char concat; heapq.nsmallest top-k instead of repeated selection scans (3×); fused count pass; finditer→findall; sliding-window tuple reuse instead of per-position slicing; zip(*iterators) allocation trim
- Artifacts: `runs/run2a-greedy-cpu/` — `ledger.jsonl`, `run.log`, `heldout.json`,
  `graph.png` (best-score curve), `tree.txt` (decorated DAG capture).
- Run 2B (nanogpt greedy) skipped for now: no CUDA GPU on this laptop; MPS works (baseline
  val loss 1.715 @ 60s budget from Phase 0) but 15–20 × 180s of MPS training wasn't a good
  use of the hackathon clock. The adapter is live-run-ready.

## Connection to the graph model

Greedy is the degenerate DAG: a single lineage of accepted nodes on `main`, every rejected or
failed attempt existing only as a ledger event. This is the control condition Phase 5
compares tree/braided against, using identical budget accounting (`max_total_runs` counts
every attempt, including failures — strategies are charged for their mistakes).

## Acceptance evidence

- Unit tests (in `tests/test_engine.py`, `tests/test_patching.py`): accept/reject/failure
  taxonomy end-to-end with a scripted proposer; patch-rejection path; scorer-crash revert;
  protected-path violation; resume-from-interrupt (2 attempts in one process, third in a
  fresh process, indices contiguous); accept-threshold arithmetic for min and max metrics;
  fuzzy patcher (no-trailing-context hunks, multi-file, creation, ambiguity, all-or-nothing,
  graph fallback integration). Full suite green.
- `braided verify-ledger --run runs/run2a-greedy-cpu`: consistent.
