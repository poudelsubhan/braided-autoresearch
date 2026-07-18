# Phase 5 summary — the experiment and the report

## Design deviation (agreed with the user mid-build)

Time-boxed to a 4-hour demo window, the 3×60 bake-off became a 3×30 bake-off that reuses
the live runs: 2A was extended from 20→30 attempts (greedy resumes idempotently), 3A (30)
served as the tree leg unchanged, and one fresh braided run (30 attempts, merge cadence 8,
k=2) completed the trio. Identical task, same acceptance rule, same budget accounting
(merge attempts charged against the 30).

## Bake-off result (cpu-optimize, 30 attempts each)

| strategy | best public (items/sec) | vs baseline | best held-out |
|---|---|---|---|
| greedy  | 4806.50 | 320× | 7405.41 |
| tree    | 3272.20 | 218× | 4831.53 |
| braided | **5826.54** | **383×** | **7976.20** |

Braided won, and the ledger shows the mechanism: at attempt 8 the merge agent composed the
redundancy-elimination lineage (2544) with the parallelism lineage (1085) into a merge node
scoring 4828 — beating greedy's entire 30-attempt total at attempt 9 — after which the
bandit correctly shifted pulls to the merge arm (m0: 4828→5322→5527→5827). Two later
re-merges of m0+b1 interfered (m0 already contained b1's ideas) and were rejected with the
interference recorded. Tree paid an exploration tax and never composed its lineages —
exactly the gap braided exists to close.

## Replication finding (honest negative)

53 accepted changes analyzed across the three runs; 22 replicated / 31 not. Mean held-out
retention: replicated 1.005, unreplicated 1.459 → effect −0.453. **The hypothesis
(replicated changes generalize better) is not supported on this task — because both groups
retained ≥100% of their gains.** The public scorer's byte-exact correctness oracle blocks
reward hacking by construction, so the detector had nothing to catch. The signal the
verifier *did* produce is still real: 10 change-classes replicated across independent
lineages (e.g. linear-scan-to-dict in all 3 lineages of the braided run). The right test of
the hypothesis is a soft-scorer task (nanogpt val loss), where public gains can be fake —
flagged as future work in the report.

## Deliverables

- `REPORT.md` + `figures/bakeoff.png` + `figures/replication.png`, interaction map (1
  compose, 2 interfere over classified pairs), per-run tree captures, limitations, future
  work (incl. the user-proposed extrapolator).
- `braided report --audit`: clean — and it earned its keep by catching a real bug before
  the report shipped (comparison curves omitted merge attempts, 27≠30 budget accounting),
  which was fixed and re-audited.
- `make bakeoff` remains available for the full 3×60 overnight replication of this design.

## Acceptance

All three runs completed on cpu-optimize; report builds; audit passes with every figure
traced to ledger data.
