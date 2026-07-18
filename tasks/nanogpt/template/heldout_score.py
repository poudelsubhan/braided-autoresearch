"""PROTECTED private scorer: evaluate the existing ckpt.pt on the held-out
split, which lives OUTSIDE the experiment repo (path via BRAIDED_HELDOUT_FILE).
Does not retrain. Never shown to agents."""

import json
import os

import score_common

if __name__ == "__main__":
    heldout = os.environ["BRAIDED_HELDOUT_FILE"]
    print(json.dumps(score_common.evaluate(heldout, train_first=False)))
