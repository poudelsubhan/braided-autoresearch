# Phase 4 summary — merge daemon and consistency verifier

## What was built

- **Merge daemon (4.1)**: `braided/agents/merger.py` + `braided/scheduler/braided.py`.
  Every `merge_cadence` attempts the braided strategy attempts one merge (charged one attempt
  of budget, with a resume guard so an interrupted run doesn't double-merge at the same
  index). **Pair selection heuristic**: among the top-3 branch heads by score, prefer the pair
  with highest file-level disjointness `1 − |files(A)∩files(B)| / |files(A)∪files(B)|` over
  each lineage's diff-from-common-ancestor; ancestor pairs and lineages with nothing accepted
  since the base are excluded. Justification: file/hunk overlap is a cheap proxy for semantic
  collision — disjoint-file pairs compose almost mechanically, so the agent's capacity is
  reserved for pairs where textual merging would fail; same-file pairs still merge when scores
  justify it, they just rank behind. The merge agent sees the common-ancestor file contents,
  both diffs-from-ancestor, and both rationale trails, and writes **one combined patch against
  the ancestor** (rewrite, not textual merge), applied on a detached checkout of the base and
  committed via `commit-tree -p A -p B` → a true two-parent merge node. Outcomes:
  **compose** (beats both parents → merge node becomes a new active arm with its own
  direction), **interfere** (scores but beats ≤1 parent → rejected, interference recorded),
  **fail** (bad patch / crash / protected path). Interference records accumulate into the
  Phase 5 interaction map.
- **Patch classifier (4.2)**: `braided/agents/classifier.py` — LLM judge labeling each
  accepted attempt with a kebab-case change-class. Consistency by construction: the prompt
  lists all previously assigned classes + summaries and instructs reuse when the mechanism
  matches; classes name mechanisms, not locations. Assignments persist as `class_assign`
  events; classification sweeps are bookkeeping (no attempt budget) and never block the loop
  (failures retry next sweep).
- **Consistency verifier (4.3)**: `braided/verify.py` — pure function over ledger + DAG, no
  LLM in the decision. A class is **replicated** iff it has accepted members in ≥ k pairwise
  independent positions: neither an ancestor of the other (full-DAG check, so merges count as
  inheritance), and no same-class member sits below both (shared-via-common-ancestor = one
  discovery, not two). Greedy max-independent-set (tiny sets; can only under-count).
  Emits `replication_tag` events (deduped, re-emitted only on growth) which drive the tree
  view's `◆` markers and the proposer's `replicated_classes_digest` — the loop is told
  "these directions replicate; these were tried once and never confirmed."
- **Held-out hook (4.4)**: `braided heldout-sweep --run <dir>` /
  `braided.report.heldout.heldout_sweep` — runs the private scorer on the root and every
  accepted node (attempts and composes), storing public/held-out/replicated per node in
  `heldout_nodes.json` (incremental on re-run). This is the Phase 5 dataset. **Leak
  enforcement**: protected paths block edits to the scorer; additionally
  `braided/privacy.py:assert_no_private_leak` is called on every prompt the proposer, merger
  (and by content-exclusion the classifier) build — the held-out command, env var, and data
  filename must never appear in agent context; the held-out data itself lives outside the
  experiment repo entirely (nanogpt) or is generated from a seed only the protected scorer
  knows (cpu-optimize).

## Deviation: no separate Live Run 4A

Per an explicit mid-build decision with the user (rate-limit economy — the experiment shares
the Claude Code login's budget), the standalone 40–60-attempt braided validation run was
dropped. Phase 4 gates on its unit tests; the plan's 4A acceptance boxes (≥1 merge attempted
with logged outcome; ≥1 class tagged replicated or an explicit "no class replicated" report;
merge commits + `◆` markers visible in the tree view) are checked instead on the braided leg
of the Phase 5 bake-off, which exercises the identical code path at larger scale. The plan
itself ranks 4.3/4.4 (the replication finding) above 4.1 (the flourish), and both are fully
unit-covered.

## Acceptance evidence

- Unit tests (`tests/test_braided.py`): independence — replication across two independent
  lineages fires with correct members/lineages; ancestor pairs never replicate;
  shared-ancestor class propagation blocked (class on b1 + two divergent children ⇒ no tag);
  tag dedup and growth-only re-emission. Merge — pair selection prefers disjoint non-ancestor
  pairs; end-to-end braided run with scripted merge agent produces a compose at exactly the
  cadence index, a true two-parent node, a registered new arm, correct budget accounting
  (4 pulls + 1 merge = 5 attempts), and a clean `verify-ledger`. Privacy — leak assert
  catches the held-out command and env var; the real cpu-optimize proposer prompt is verified
  clean and free of protected-scorer content.
- Full suite: 51 tests green at gate time.
