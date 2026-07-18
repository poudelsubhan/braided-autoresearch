# Phase 1 summary — graph substrate and ledger

## What was built

- **Graph API (1.1)**: `braided/graph.py` — `Graph` wraps the experiment repo with
  `nodes()` / `leaves()` / `branches()` / `lineage()` / `is_ancestor()` / `create_branch()` /
  `checkout()` / `apply_patch()` / `commit_patch()` / `merge_commit()`. Scores live in git
  notes namespace `refs/notes/score`; per-node metadata (branch, rationale, kind:
  baseline|attempt|merge) in `refs/notes/meta`. `merge_commit(sha_a, sha_b, patch, ...)`
  builds a **true two-parent merge commit** by checking out the merge-base, applying the
  agent-written combined patch, `write-tree` + `commit-tree -p a -p b` — git's textual merge
  machinery is never invoked. `apply_patch` enforces protected paths (raising
  `ProtectedPathViolation` *before* touching the tree) and turns malformed diffs into
  `PatchError`, both non-fatal, ledger-visible failures for the loop.
- **Ledger (1.2)**: `braided/ledger.py` — append-only `ledger.jsonl`, pydantic events:
  `attempt`, `merge_attempt`, `replication_tag`, plus two the later phases need:
  `class_assign` (classifier groundwork) and `branch` (fork/prune lifecycle with the
  direction hint — needed for the tree view's "pruned" dimming and Phase 3's scheduler).
  Every event is timestamped and stamped with the run id. `verify_ledger()` walks the DAG
  and cross-checks: every non-root node has a matching accepted-attempt (or compose
  merge_attempt) event and a score note; every ledger sha exists in the DAG. Wired to
  `braided verify-ledger --run <dir>`.
- **Agent context queries (1.3)**: `braided/context.py` — `rationale_trail` (last n accepted
  rationales + scores on the first-parent lineage), `sibling_digest` (one line per other
  branch head), `failure_digest` (recent rejected/failed attempts with failure kinds, so the
  proposer stops repeating dead ends), `replicated_classes_digest` (reads
  `replication_tag` events; empty until Phase 4).
- **Tree view (1.4)**: `braided/report/tree.py` + `braided report --tree --run <dir>` —
  `git log --graph` draws topology; each commit line is rewritten to
  `<short-sha> [score] (branch) <rationale[:50]>` with ANSI green/cyan(merge)/dim(pruned)
  and a `◆` marker for replicated-class members (live as soon as Phase 4 emits tags).

## Key design decisions

- **`--branches`, not `--all`**: notes commits are parentless, so `--all` traversals saw
  phantom roots. All DAG walks use `--branches`; consequently every merge node must (and
  does) get a branch ref — which the design wants anyway, since a composed merge becomes a
  new active arm.
- **`lineage()` is first-parent**: "the path back to root" through merges follows the
  first parent (the merge's primary lineage). Independence checks in Phase 4 use full-DAG
  `is_ancestor`, which is the correct relation for that purpose.
- **Scoring-before-commit model**: only accepted attempts become commits; rejected/failed
  attempts exist solely as ledger events. `verify_ledger` therefore checks
  commits ⊆ accepted events, and the attempt history's completeness lives in the ledger.
- **Baseline is a decorated root**: `init_task` now writes the calibration mean as the root's
  score note (+ meta), so the DAG is self-describing from birth. The two Phase-0 acceptance
  runs were backfilled.

## Connection to the graph model

Nodes = commits + score notes; edges = parent links whose content is the applied diff and
whose rationale rides in the meta note; the ledger is the sidecar recording *every* attempt
(including the ones that never became nodes) and is machine-checkable against git.

## Acceptance evidence

- Scripted test `test_synthetic_three_branch_dag_with_merge`: 3 branches + one agent-written
  two-parent merge built via the Graph API only; `verify_ledger` returns no problems;
  `render_tree` output contains scores, rationales, and git's merge diamond; `leaves()` and
  `lineage()` return exactly the expected sets; context digests contain the right entries.
- Negative tests: missing attempt event and missing score note are both caught by
  `verify-ledger`; protected-path patch refused with tree untouched; malformed/empty patches
  raise `PatchError`.
- Ledger restart test: re-open + append across `Ledger` instances; `next_attempt_index()`
  resumes correctly.
- Full suite: 19 tests green.
