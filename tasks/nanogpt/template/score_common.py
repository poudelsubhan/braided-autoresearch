"""PROTECTED. Shared evaluation logic for the nanogpt scorers.

Runs the (editable) trainer as a subprocess, then evaluates the checkpoint
itself: cross-entropy computed HERE from the model's logits, deterministically,
over sequential windows covering the whole eval text. The trainer supplies the
architecture (class GPT) but never the loss computation.
"""

from __future__ import annotations

import json
import subprocess
import sys


def evaluate(eval_text_path: str, train_first: bool = True) -> dict:
    if train_first:
        proc = subprocess.run([sys.executable, "train.py"], capture_output=True, text=True)
        sys.stderr.write(proc.stdout[-2000:] + proc.stderr[-2000:])
        if proc.returncode != 0:
            print(f"TRAINING FAILED: exit {proc.returncode}")
            raise SystemExit(1)

    import torch
    from torch.nn import functional as F

    import train as trainer  # the candidate under test

    with open("meta.json") as f:
        chars = json.load(f)["chars"]
    stoi = {c: i for i, c in enumerate(chars)}

    with open(eval_text_path) as f:
        text = f.read()
    data = torch.tensor([stoi[c] for c in text if c in stoi], dtype=torch.long)

    ckpt = torch.load("ckpt.pt", map_location="cpu")
    model = trainer.GPT(**ckpt["model_config"])
    model.load_state_dict(ckpt["model_state"])
    model.eval()

    device = (
        "cuda" if torch.cuda.is_available()
        else "mps" if torch.backends.mps.is_available()
        else "cpu"
    )
    model.to(device)

    block = int(ckpt["model_config"]["block_size"])
    batch = 32
    total_loss, total_tokens = 0.0, 0
    windows = []
    for start in range(0, len(data) - 1, block):
        chunk = data[start : start + block + 1]
        if len(chunk) < 2:
            continue
        windows.append(chunk)

    with torch.no_grad():
        for i in range(0, len(windows), batch):
            group = windows[i : i + batch]
            maxlen = max(len(c) for c in group)
            # pad by truncating to the shortest in group instead: keep exactness by
            # evaluating equal-length groups; the last ragged window goes alone.
            equal = [c for c in group if len(c) == maxlen]
            ragged = [c for c in group if len(c) != maxlen]
            for subgroup in ([equal] if equal else []) + [[c] for c in ragged]:
                x = torch.stack([c[:-1] for c in subgroup]).to(device)
                y = torch.stack([c[1:] for c in subgroup]).to(device)
                logits = model(x)
                loss = F.cross_entropy(
                    logits.reshape(-1, logits.size(-1)), y.reshape(-1), reduction="sum"
                )
                total_loss += loss.item()
                total_tokens += y.numel()

    return {"score": round(total_loss / total_tokens, 6), "eval_tokens": total_tokens}
