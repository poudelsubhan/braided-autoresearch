# Phase 0 summary — scaffold and task substrate

## What was built

- **Scaffold (0.1)**: package `braided/` (`config`, `runner`, `tasks`, `cli`, plus empty
  `scheduler/`, `agents/`, `report/` packages for later phases), `pyproject.toml` (uv-managed,
  Python 3.12, hatchling build, `braided` console script), pytest config, README, `.gitignore`
  (ignores `runs/`).
- **Config schema (0.2)**: `braided/config.py` — pydantic `TaskConfig` / `SearchConfig` /
  `RunConfig`, loadable from and savable to a single `run.yaml`. `TaskConfig.direction`
  (`min`/`max`) plus `better()` / `improvement()` helpers so no other module ever hardcodes
  score polarity. A model validator rejects configs where the private scorer command equals the
  public one (first layer of the "private scorer never leaks" defense).
- **`nanogpt-shakespeare` adapter (0.3)**: `tasks/nanogpt/template/` — single-file char-GPT
  trainer `train.py` (~230 lines, editable), protected `score.py`/`heldout_score.py`/
  `score_common.py`/`val.txt`/`meta.json`. Tinyshakespeare downloaded at init time and split
  85/7.5/7.5 into train/val/held-out.
- **`cpu-optimize` adapter (0.4)**: `tasks/cpu-optimize/template/` — deliberately naive word
  n-gram top-k pipeline (`pipeline.py`, editable) + protected scorers. Score =
  documents/second on a fixed seeded public corpus, after asserting output equality with a fast
  reference implementation.
- **Sandbox runner (0.5)**: `braided/runner.py` — runs a scorer command in the experiment
  worktree with wall-clock timeout (budget + 30s grace), persisted stdout/stderr, and the full
  failure taxonomy `timeout | crash | invalid-output` as structured `ScoreResult`s;
  `find_protected_violations()` implements the `protected-path-violation` check applied before
  any patch lands (wired into patch application in Phase 2).
- **`braided init-task <task>`**: creates `runs/<run-id>/` containing a fresh **experiment
  repo** (`repo/`, its own git history, baseline commit), `run.yaml`, `logs/`, and
  `baseline.json` with N calibration scores + mean/std.

## Key design decisions (and deviations from the plan)

- **subprocess git, not GitPython.** Everything we need is porcelain-level (`init`, `add`,
  `commit`, later `branch`/`merge`/`notes`/`log --graph`); shelling out keeps zero abstraction
  between us and the exact git behavior the DAG design depends on (two-parent merge commits,
  notes namespaces), and makes failures directly debuggable. GitPython would add a dependency
  to wrap the same subprocess calls.
- **Golden outputs are computed, not stored.** The plan said "golden outputs"; instead
  `score_common.py` (protected) regenerates the corpus deterministically from a seed and
  computes the expected answer with an embedded fast reference implementation at score time.
  Same anti-cheat property (byte-exact correctness assert), no data files to manage, and the
  hidden held-out distribution needs no hidden files at all for this task.
- **Held-out data lives outside the experiment repo** (nanogpt): `heldout.txt` is written to
  the run dir, never the repo; the private scorer finds it via `BRAIDED_HELDOUT_FILE`, set
  only by `braided.tasks.heldout_env()`. The split logic is in the protected init path. An
  agent editing `train.py` physically cannot see held-out text. (`val.txt` *is* visible and
  protected-from-edit but trainable-on — deliberately: overfitting the public metric is the
  reward hack the held-out scorer is designed to catch.)
- **Scoring contract for nanogpt**: the scorer runs `python train.py` as a subprocess, then
  loads `ckpt.pt`, rebuilds the (agent-defined) `GPT` class, and computes cross-entropy
  **itself** over sequential windows of the eval text. The trainer supplies architecture, never
  the loss computation, so "return loss=0" is not an available cheat.
- **cpu-optimize workload sizing**: tuned to ~3.3s/naive public run (50 docs × 1200 words,
  4000-word vocab) — long enough that run-to-run noise is ~1%, short enough for 60-attempt
  runs. The naive implementation's cost is dominated by linear-scan n-gram counting
  (`list.index`), leaving ~50× headroom for dict-based rewrites plus many smaller wins
  (string building, stopword set, heap top-k).
- **Interpreter**: system Python is 3.9, so the harness pins uv-managed CPython 3.12 in
  `.venv`; the runner prepends the venv's bin dir to scorer PATH so `python score.py` in task
  configs resolves to the right interpreter. torch is an optional dependency group
  (`--extra nanogpt`), installed (2.13.0, MPS backend works).
- **Budget override**: `init-task --budget` exists so the 180s nanogpt default can be reduced
  for laptop calibration (acceptance used 60s on MPS).

## How this connects to the graph model

The experiment repo created by `init-task` is the future DAG substrate: its baseline commit is
the root node. `baseline.json`'s mean/std is the noise floor that Phase 2's acceptance
threshold will be computed from. `ScoreResult`s become node scores (git notes) and ledger
`attempt` events in Phase 1. `find_protected_violations` is the gate every proposed patch (edge)
must pass before it can become a commit.

## Acceptance evidence

- `braided init-task cpu-optimize` (full size, calibrate=3): baseline mean **15.158
  items/sec**, std **0.191** (1.26% relative). Accept threshold at 1.5×std ⇒ ~0.29 items/sec.
- `braided init-task nanogpt-shakespeare --budget 60` (MPS, calibrate=2): baseline val loss
  mean **1.7150**, std **0.0058** (0.34% relative). Variance comes from wall-clock budgeting
  (differing step counts) + MPS nondeterminism, despite fixed seeds.
- `pytest`: 12 tests green — config round-trip + polarity helpers + private-scorer-leak
  validator; runner failure taxonomy (ok/crash/invalid-output/timeout, JSON parsing edge
  cases); protected-path enforcement incl. globs; naive-vs-reference pipeline equality with
  tie-break ordering; deterministic corpus generation; end-to-end `init_task` on a tiny
  workload including a held-out scorer run.
