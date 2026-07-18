# cpu-optimize

Optimize the throughput of `pipeline.py` without changing its observable behavior.

`pipeline.top_ngrams(docs, n, k)` takes a list of text documents and returns the
top-k word n-grams by count, as a list of `(ngram_string, count)` tuples, ordered
by count descending, ties broken by the n-gram string ascending.

Tokenization rules (must be preserved exactly):
- a token is a maximal run of alphanumeric characters, lowercased;
- tokens appearing in `pipeline.STOPWORDS` are dropped;
- n-grams are formed over the surviving tokens of each document independently
  (n-grams never span documents), joined with a single space.

The scorer (`python score.py`) benchmarks `top_ngrams` on a fixed public input
set, asserts the output exactly matches a reference implementation, and prints
`{"score": <items_per_sec>}` — documents processed per second, higher is better.
An incorrect output scores nothing (the run fails).

You may restructure `pipeline.py` freely (data structures, algorithms, caching)
as long as outputs stay byte-identical. `score.py`, `heldout_score.py`, and
`score_common.py` are protected — do not modify them.
