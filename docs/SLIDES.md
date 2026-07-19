# Slide deck spec — "Braided Autoresearch"

Instructions for the generator: produce a 7-slide 16:9 deck. Dark theme (near-black
background #0d1117, light text #e6edf3, accent green #7ee787, accent blue #58a6ff, accent
red #f85149 — matching a git/terminal aesthetic). Monospace font for code and numbers.
Big numbers should be BIG. Insert the two images from the given file paths
(`figures/bakeoff.png`, `figures/replication.png`); if unavailable, leave a placeholder
box with the caption. Speaker notes go in the notes field of each slide.

---

## Slide 1 — Title

**Title:** Braided Autoresearch
**Subtitle:** AI research as a git DAG: branch, merge, replicate
**Footer:** built in one day with Claude Code · every number auditable from run ledgers

**Speaker notes:** Autoresearch today is a straight line: propose a change, keep or
revert, repeat. I turned the line into a graph, and the graph wins.

---

## Slide 2 — The task

**Title:** The task: optimize code that cannot be cheated

**Body (3 bullets):**
- Deliberately naive text pipeline: **15 docs/sec** (linear scans, char-by-char strings)
- Goal: make it fast, **byte-identical output** — scorer diffs against a reference
  implementation, wrong output = disqualified
- Scorer is protected: patches touching it are auto-rejected; AI never sees it

**Visual:** small code snippet in monospace:
```python
if gram in keys:                 # O(n) scan,
    idx = keys.index(gram)       # ...twice
    counts[idx] = counts[idx] + 1
```

**Speaker notes:** The correctness oracle matters twice: it blocks metric-gaming, and it
becomes the punchline of the honest-negative slide at the end.

---

## Slide 3 — The system

**Title:** Research as a git DAG

**Body (4 short bullets, each pairing a component with its git primitive):**
- **Lineages = branches** — each born with its own research direction (data structures /
  redundancy / parallelism), drawn by the model
- **A UCB bandit** decides which lineage gets the next attempt
- **Semantic merges = two-parent merge commits** — an agent rewrites one combined patch
  against the common ancestor (not a textual merge)
- **Replication = DAG ancestry math** — same mechanism discovered in provably independent
  lineages gets a ◆ tag; no LLM in the verdict

**Footer line:** nodes = commits · scores = git notes · every attempt in an append-only
ledger, cross-checked by `verify-ledger`

**Speaker notes:** Everything is stock git. A judge can clone a run and audit it with
`git log --graph`.

---

## Slide 4 — The moment that matters

**Title:** Attempt 8: the merge that beat the baseline's whole run

**Visual:** a simple diagram (three boxes converging): lineage b1 "redundancy elimination —
2,544/s" and lineage b2 "parallelism — 1,085/s" merging into a diamond node "merge m0 —
**4,828/s**", with an arrow continuing to "**5,827/s** after bandit exploitation".

**Callout box:** "Composed at attempt 8 → beat greedy's entire 30-attempt run by attempt 9"

**Speaker notes:** Neither branch had both ideas. The merge agent rewrote them into one
patch against the common ancestor; a real two-parent commit. The bandit noticed the new
arm was best and exploited it. Two later re-merges *interfered* (m0 already contained
b1's ideas) — rejected, and the interference is recorded as data.

---

## Slide 5 — The experiment

**Title:** Same budget, three strategies

**Visual:** insert image `figures/bakeoff.png` (best-score-vs-attempts curves).

**Table (big numbers):**
| strategy | best (docs/sec) | vs baseline |
|---|---|---|
| greedy (straight line) | 4,806 | 320× |
| tree (branches, no merge) | 3,272 | 218× |
| **braided (branch + merge + replicate)** | **5,827** | **383×** |

**Footer:** hidden held-out scorer confirms: 40 → **7,976 docs/sec** — real gains, not
benchmark overfit

**Speaker notes:** Tree pays an exploration tax and never composes its findings — that gap
is exactly what the merge closes. Held-out = different seed, distribution, and n-gram
order, never visible to the model.

---

## Slide 6 — The honest finding

**Title:** The reward-hacking detector found... no hacking

**Body:**
- Hypothesis: ◆-replicated changes survive the hidden test better (replication = free
  reward-hacking detector)
- Result: **not supported here — both groups kept ≥100% of their gains** (n=53: 22
  replicated, 31 not)
- Why: the byte-exact correctness oracle makes cheating impossible **by construction** —
  there was nothing to catch
- The real test: soft-scored tasks (validation loss) where fake gains are possible — the
  nanogpt harness is already built

**Optional visual:** insert image `figures/replication.png` at reduced size.

**Footer:** every figure auto-audited against run ledgers — the audit caught a real
accounting bug in this deck's own chart before shipping

**Speaker notes:** Reporting the negative with its mechanism is the credibility play. The
replication *signal* itself was real: 10 mechanisms independently rediscovered, e.g. all
three lineages invented the dict swap on their own.

---

## Slide 7 — Close

**Title:** Branch. Merge. Replicate. Audit.

**Body (3 bullets):**
- One day, ~2,600 lines: bandit scheduler, merge agent, replication verifier, ledger with
  cross-checking audit, live TUI + live DAG page
- Findings: **semantic merges are where the win is (383×)**; replication verification
  needs soft scorers to shine
- Next: nanogpt series · extrapolator (replication tags trigger bigger steps) · hardened
  sandbox

**Footer:** live demo running behind this deck — `runs/demo/graph.html`

**Speaker notes:** End on the live page if the run produced a merge diamond during the
talk; otherwise end on slide 5's table.
