"""PROTECTED public scorer: train within budget, then val loss on val.txt."""

import json

import score_common

if __name__ == "__main__":
    print(json.dumps(score_common.evaluate("val.txt", train_first=True)))
