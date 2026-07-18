"""PROTECTED private scorer. Hidden input distribution (different seed, shape,
vocabulary size, and n-gram order). Never shown to agents."""

import json
import os

import score_common

if __name__ == "__main__":
    result = score_common.benchmark(
        seed=987654,
        n_docs=int(os.environ.get("BRAIDED_CPU_HELDOUT_DOCS", "40")),
        words_per_doc=650,
        vocab_size=5000,
        n=3,
        k=80,
    )
    print(json.dumps(result))
