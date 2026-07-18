"""PROTECTED. Data generation, reference implementation, benchmark harness.

The inner loop must never edit this file (or score.py / heldout_score.py).
Correctness is enforced by comparing pipeline output against the reference
implementation on the exact benchmark inputs — a patch that changes observable
behavior fails the run, so "make it return less" is not an available shortcut.
"""

from __future__ import annotations

import os
import random
import re
import time
from collections import Counter

_WORD_RE = re.compile(r"[0-9a-zA-Z]+")


def make_corpus(
    seed: int,
    n_docs: int,
    words_per_doc: int,
    vocab_size: int,
    punct_every: int = 9,
) -> list[str]:
    """Deterministic pseudo-text: Zipf-ish word draws from a seeded vocabulary."""
    rng = random.Random(seed)
    alphabet = "abcdefghijklmnopqrstuvwxyz"
    vocab = []
    seen = set()
    while len(vocab) < vocab_size:
        w = "".join(rng.choice(alphabet) for _ in range(rng.randint(3, 9)))
        if w not in seen:
            seen.add(w)
            vocab.append(w)
    # Zipf-ish: rank r gets weight 1/(r+1)
    weights = [1.0 / (r + 1) for r in range(vocab_size)]
    punct = [". ", ", ", "; ", "! ", "? ", ": ", " -- ", "\n"]
    docs = []
    for _ in range(n_docs):
        parts = []
        picks = rng.choices(vocab, weights=weights, k=words_per_doc)
        for i, w in enumerate(picks):
            if rng.random() < 0.08:
                w = w.capitalize()
            parts.append(w)
            if (i + 1) % punct_every == 0:
                parts.append(rng.choice(punct))
            else:
                parts.append(" ")
        docs.append("".join(parts))
    return docs


def reference_top_ngrams(docs: list[str], n: int, k: int, stopwords) -> list[tuple[str, int]]:
    """Fast reference implementation of the pipeline contract."""
    stop = set(stopwords)
    counts: Counter[str] = Counter()
    for doc in docs:
        tokens = [t for t in (m.group(0).lower() for m in _WORD_RE.finditer(doc)) if t not in stop]
        counts.update(" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1))
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[:k]


def benchmark(seed: int, n_docs: int, words_per_doc: int, vocab_size: int, n: int, k: int) -> dict:
    """Generate inputs, check correctness against the reference, time the pipeline.

    Returns the score dict to print as JSON. Raises SystemExit(1) on a
    correctness failure so the runner records a crash (score = nothing).
    """
    import pipeline  # the candidate under test, from the experiment worktree cwd

    docs = make_corpus(seed=seed, n_docs=n_docs, words_per_doc=words_per_doc, vocab_size=vocab_size)
    expected = reference_top_ngrams(docs, n=n, k=k, stopwords=pipeline.STOPWORDS)

    repeats = int(os.environ.get("BRAIDED_SCORE_REPEATS", "3"))
    budget = float(os.environ.get("BRAIDED_BUDGET_SECONDS", "120"))
    deadline = time.perf_counter() + budget

    best_elapsed = None
    got = None
    for r in range(repeats):
        start = time.perf_counter()
        got = pipeline.top_ngrams(docs, n=n, k=k)
        elapsed = time.perf_counter() - start
        if got != expected:
            print("CORRECTNESS FAILURE: pipeline output does not match reference")
            for i, (g, e) in enumerate(zip(got or [], expected)):
                if g != e:
                    print(f"  first mismatch at rank {i}: got {g!r}, expected {e!r}")
                    break
            else:
                print(f"  length mismatch: got {len(got or [])}, expected {len(expected)}")
            raise SystemExit(1)
        if best_elapsed is None or elapsed < best_elapsed:
            best_elapsed = elapsed
        if time.perf_counter() + elapsed > deadline and r + 1 >= 1:
            break  # don't start a repeat we can't finish inside the budget

    return {
        "score": round(n_docs / best_elapsed, 3),  # items (docs) per second, best of repeats
        "elapsed_best": round(best_elapsed, 4),
        "n_docs": n_docs,
    }
