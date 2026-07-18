"""PROTECTED public scorer. Fixed public input set; prints {"score": items_per_sec}."""

import json
import os

import score_common

if __name__ == "__main__":
    result = score_common.benchmark(
        seed=1234,
        n_docs=int(os.environ.get("BRAIDED_CPU_DOCS", "50")),
        words_per_doc=1200,
        vocab_size=4000,
        n=2,
        k=50,
    )
    print(json.dumps(result))
