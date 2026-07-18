# nanogpt-shakespeare

Minimize the validation loss of a character-level GPT trained on Shakespeare
**within a fixed wall-clock training budget** (`BRAIDED_BUDGET_SECONDS`, default
180s). Efficiency wins, not scale: a change only helps if it reaches a lower val
loss inside the same budget.

Files:
- `train.py` — the trainer. **Editable.** Architecture, optimizer, schedule,
  batching, data pipeline: all fair game.
- `train.txt` — training text. Editable (data-centric changes allowed).
- `val.txt`, `meta.json` — validation split and character vocabulary. Protected.
- `score.py`, `heldout_score.py` — scorers. Protected.

Contract `train.py` must keep (the scorer depends on it):
1. Running `python train.py` trains within the wall-clock budget from the env
   var `BRAIDED_BUDGET_SECONDS` and writes `ckpt.pt` containing
   `{"model_config": <dict>, "model_state": <state_dict>}`.
2. `train.py` defines a class `GPT` constructible as `GPT(**model_config)` whose
   `forward(idx)` takes a LongTensor `(B, T)` of character ids (vocabulary and
   id order fixed by `meta.json`) and returns logits `(B, T, vocab_size)`.
3. `model_config` must include `block_size` (max context length).

The scorer runs `python train.py`, then loads `ckpt.pt`, rebuilds `GPT`, and
computes cross-entropy on `val.txt` itself. It prints
`{"score": <val_loss>}` — lower is better.
