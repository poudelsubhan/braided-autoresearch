# Demo runbook — 3-minute script

## One-sentence pitch

Karpathy-style autoresearch is a straight line: propose, keep or revert, repeat. I turned
it into a **git DAG**: a bandit decides which research lineage to grow, an agent writes
semantic merges between lineages as true two-parent merge commits, and when independent
lineages rediscover the same mechanism I tag it **replicated**. Self-consistency as a
built-in reward-hacking detector.

---

## Setup (do this ~10 minutes before, takes 5)

```sh
# Terminal 1 — create and start the live demo run (one command, no yaml editing)
uv run braided init-task cpu-optimize --run-id demo --strategy braided --merge-cadence 5 --attempts 30
uv run braided run --config runs/demo/run.yaml --tui

# Terminal 2 — the projector views. Serve the repo root once; open TWO tabs:
#   live run:      http://localhost:8123/runs/demo/graph.html
#   finished run:  http://localhost:8123/runs/run5-braided-cpu/graph.html
python -m http.server 8123

# Terminal 3 — backups / extras
uv run braided report --tree --run runs/run5-braided-cpu   # terminal fallback
open REPORT.md figures/bakeoff.png figures/replication.png
cat tasks/cpu-optimize/template/pipeline.py
```

Windows to have open, in presentation order:
1. `pipeline.py` in your editor (the naive code)
2. Browser: `graph.html` of the **live** demo run
3. Terminal 3 (for the finished braided DAG)
4. `figures/bakeoff.png` and `REPORT.md`

---

## The script (timed, word-for-word beats)

### 0:00–0:25 — The problem and the task

**SHOW:** `pipeline.py` (scroll slowly through `count_ngrams` — the `keys.index(gram)` line).

**SAY:**
> "This is a deliberately awful text-processing program — it counts phrases across
> documents using linear list scans and character-by-character string building. It does
> 15 documents a second. The task I give the AI: make it faster **without changing a
> single byte of output**. A protected scorer times every candidate and diffs its output
> against a reference implementation — wrong answer, instant disqualification. The AI can
> never see or touch the scorer. So there is no way to cheat the metric; the only way to
> score is real engineering."

### 0:25–1:00 — The loop, live

**SHOW:** browser, `graph.html` of the live run — nodes growing on branches.

**SAY:**
> "This is running live right now. Each green node is a code change Claude proposed that
> beat the noise floor and got committed. And I mean *committed* — the whole search **is a
> git repository**. Nodes are commits, scores ride in git notes, and these branches are
> parallel research lineages, each with its own assignment the model drew at birth — one
> explores data structures, one eliminates redundant work, one tries parallelism. A UCB
> bandit watches which lineage is paying off and gives it the next turn."

**POINT AT** one node's tooltip (hover): read the rationale aloud —
> "Every change ships with a mechanism, not a description: 'replace the linear scan with a
> dict because inserts dominate.' Rejected and crashed attempts land in an append-only
> ledger — `verify-ledger` cross-checks the ledger against the git DAG, so every number I
> show you is auditable."

### 1:00–1:50 — The headline result: the merge

**SHOW:** browser, second tab — the **finished** run's graph
(`runs/run5-braided-cpu/graph.html`). Point at the blue merge node m0 (two incoming
edges) and the red ◆ markers. (Terminal fallback: `braided report --tree`.)

**SAY:**
> "Here's a finished 30-attempt run, and here's the moment that makes this a DAG and not a
> tree. At attempt 8, the merge agent took the two best lineages — redundancy elimination
> at 2,544 docs/sec and parallelism at 1,085 — and **rewrote one combined patch against
> their common ancestor**. Not a textual git merge; a semantic rewrite, committed as a true
> two-parent merge commit. That composed node scored **4,828 — which beat what the
> straight-line baseline achieved in its entire 30-attempt run, at attempt 9.** The bandit
> then piled onto the merged lineage and rode it to **5,827 — 383× the baseline.**"
>
> "And these diamonds ◆ mark **replication**: mechanisms that multiple lineages discovered
> *independently* — provably independent, it's a pure ancestry computation on the DAG, no
> LLM involved. All three lineages independently invented 'swap the list scan for a dict.'"

### 1:50–2:30 — The experiment

**SHOW:** `figures/bakeoff.png`, then the bake-off table in `REPORT.md`.

**SAY:**
> "Same task, same budget, three strategies. Straight-line greedy: 320×. Tree without
> merging: 218× — it pays an exploration tax and never composes what it finds. **Braided —
> tree plus merges plus replication tracking — wins at 383×.** The composition is where the
> win comes from."
>
> "And one more check: a second, *hidden* scorer the AI never sees — different data,
> different distribution. The optimized code scores **7,976 docs/sec there, up from 40**.
> The gains are real, not overfit to the visible benchmark."

### 2:30–3:00 — The honest finding + close

**SHOW:** `figures/replication.png` / section 2 of `REPORT.md`.

**SAY:**
> "My headline hypothesis was that replicated changes would survive the hidden test better
> — replication as a free reward-hacking detector. On this task: **not supported, and the
> reason is the interesting part** — *nothing* failed the hidden test, because the
> correctness oracle makes cheating impossible by construction. The detector had nothing
> to catch. The right test is a soft-scored task like neural-net validation loss, where
> fake gains are possible — the harness for that is already built. I report the negative
> because every figure in this report is auto-audited against the run ledgers — the audit
> even caught a real accounting bug in my comparison plot before I shipped it."
>
> "So: research as a git DAG — branch, merge, replicate — with a bandit steering and a
> paper trail a judge can verify. Thanks."

---

## Q&A ammunition

- **"How do you know it's not cheating?"** → three layers: byte-exact correctness assert in
  the scorer, protected-path enforcement (patches touching scorer files are rejected
  pre-apply, logged as `protected-path-violation`), and `braided/privacy.py` asserts the
  held-out command/path never appears in any prompt.
- **"Why did tree lose to greedy?"** → exploration tax on a single-hill task; the point of
  branching isn't raw score, it's what merging and replication buy you — and braided (tree
  + merges) beat greedy.
- **"What's novel vs. AlphaEvolve-style population search?"** → semantic merges as
  first-class two-parent git commits; replication-as-verification computed from DAG
  ancestry; the whole thing auditable in stock git tooling.
- **"Next step?"** → nanogpt series (soft scorer → real test of the reward-hacking
  detector) and an extrapolator: when a mechanism replicates, stop nibbling and push it to
  its endpoint in one large patch.
- **Rate limit / Wi-Fi dies mid-demo** → the live page renders from disk; skip to the
  finished runs. Every visual works offline.

---

## PowerPoint

`docs/SLIDES.md` is a self-contained slide-by-slide spec (content, speaker notes, image
paths). Paste it into Claude Desktop and ask for a pptx; the two figure PNGs it references
are in `figures/`.
