"""Minimal char-level GPT trainer (nanoGPT-style, single file).

Trains within a wall-clock budget (env BRAIDED_BUDGET_SECONDS) and writes
ckpt.pt = {"model_config": dict, "model_state": state_dict}.
See README.md for the contract the scorer depends on.
"""

import json
import math
import os
import time

import torch
import torch.nn as nn
from torch.nn import functional as F

# ----------------------------------------------------------------------------
# hyperparameters — tune freely
n_layer = 4
n_head = 4
n_embd = 128
block_size = 128
dropout = 0.1
batch_size = 32
learning_rate = 3e-3
weight_decay = 0.1
grad_clip = 1.0
eval_interval = 100  # steps between val-loss probes (for logging/best-ckpt only)
eval_batches = 8
seed = 1337
# ----------------------------------------------------------------------------


class CausalSelfAttention(nn.Module):
    def __init__(self, n_embd, n_head, block_size, dropout):
        super().__init__()
        assert n_embd % n_head == 0
        self.n_head = n_head
        self.c_attn = nn.Linear(n_embd, 3 * n_embd)
        self.c_proj = nn.Linear(n_embd, n_embd)
        self.attn_dropout = nn.Dropout(dropout)
        self.resid_dropout = nn.Dropout(dropout)

    def forward(self, x):
        B, T, C = x.size()
        q, k, v = self.c_attn(x).split(C, dim=2)
        hs = C // self.n_head
        q = q.view(B, T, self.n_head, hs).transpose(1, 2)
        k = k.view(B, T, self.n_head, hs).transpose(1, 2)
        v = v.view(B, T, self.n_head, hs).transpose(1, 2)
        y = F.scaled_dot_product_attention(
            q, k, v, dropout_p=self.attn_dropout.p if self.training else 0.0, is_causal=True
        )
        y = y.transpose(1, 2).contiguous().view(B, T, C)
        return self.resid_dropout(self.c_proj(y))


class Block(nn.Module):
    def __init__(self, n_embd, n_head, block_size, dropout):
        super().__init__()
        self.ln1 = nn.LayerNorm(n_embd)
        self.attn = CausalSelfAttention(n_embd, n_head, block_size, dropout)
        self.ln2 = nn.LayerNorm(n_embd)
        self.mlp = nn.Sequential(
            nn.Linear(n_embd, 4 * n_embd),
            nn.GELU(),
            nn.Linear(4 * n_embd, n_embd),
            nn.Dropout(dropout),
        )

    def forward(self, x):
        x = x + self.attn(self.ln1(x))
        x = x + self.mlp(self.ln2(x))
        return x


class GPT(nn.Module):
    def __init__(self, vocab_size, n_layer, n_head, n_embd, block_size, dropout=0.0):
        super().__init__()
        self.block_size = block_size
        self.tok_emb = nn.Embedding(vocab_size, n_embd)
        self.pos_emb = nn.Embedding(block_size, n_embd)
        self.drop = nn.Dropout(dropout)
        self.blocks = nn.ModuleList(
            [Block(n_embd, n_head, block_size, dropout) for _ in range(n_layer)]
        )
        self.ln_f = nn.LayerNorm(n_embd)
        self.head = nn.Linear(n_embd, vocab_size, bias=False)
        self.apply(self._init_weights)

    def _init_weights(self, module):
        if isinstance(module, nn.Linear):
            nn.init.normal_(module.weight, mean=0.0, std=0.02)
            if module.bias is not None:
                nn.init.zeros_(module.bias)
        elif isinstance(module, nn.Embedding):
            nn.init.normal_(module.weight, mean=0.0, std=0.02)

    def forward(self, idx):
        B, T = idx.size()
        pos = torch.arange(T, device=idx.device)
        x = self.drop(self.tok_emb(idx) + self.pos_emb(pos))
        for block in self.blocks:
            x = block(x)
        return self.head(self.ln_f(x))


def pick_device():
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def encode(text, stoi):
    return torch.tensor([stoi[c] for c in text if c in stoi], dtype=torch.long)


def get_batch(data, batch_size, block_size, device, generator):
    ix = torch.randint(len(data) - block_size - 1, (batch_size,), generator=generator)
    x = torch.stack([data[i : i + block_size] for i in ix])
    y = torch.stack([data[i + 1 : i + 1 + block_size] for i in ix])
    return x.to(device), y.to(device)


@torch.no_grad()
def estimate_val_loss(model, val_data, device, generator):
    model.eval()
    losses = []
    for _ in range(eval_batches):
        x, y = get_batch(val_data, batch_size, block_size, device, generator)
        logits = model(x)
        losses.append(F.cross_entropy(logits.view(-1, logits.size(-1)), y.view(-1)).item())
    model.train()
    return sum(losses) / len(losses)


def main():
    budget = float(os.environ.get("BRAIDED_BUDGET_SECONDS", "180"))
    deadline = time.time() + budget

    torch.manual_seed(seed)
    device = pick_device()

    with open("meta.json") as f:
        chars = json.load(f)["chars"]
    stoi = {c: i for i, c in enumerate(chars)}
    vocab_size = len(chars)

    with open("train.txt") as f:
        train_data = encode(f.read(), stoi)
    with open("val.txt") as f:
        val_data = encode(f.read(), stoi)

    model_config = dict(
        vocab_size=vocab_size,
        n_layer=n_layer,
        n_head=n_head,
        n_embd=n_embd,
        block_size=block_size,
        dropout=dropout,
    )
    model = GPT(**model_config).to(device)
    model.train()
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=learning_rate, weight_decay=weight_decay, betas=(0.9, 0.99)
    )
    gen = torch.Generator().manual_seed(seed)

    def save(state):
        torch.save({"model_config": model_config, "model_state": state}, "ckpt.pt")

    best_val = float("inf")
    step = 0
    while time.time() < deadline:
        x, y = get_batch(train_data, batch_size, block_size, device, gen)
        logits = model(x)
        loss = F.cross_entropy(logits.view(-1, logits.size(-1)), y.view(-1))
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        if grad_clip:
            torch.nn.utils.clip_grad_norm_(model.parameters(), grad_clip)
        optimizer.step()
        step += 1
        if step % eval_interval == 0:
            vl = estimate_val_loss(model, val_data, device, gen)
            print(f"step {step} train {loss.item():.4f} val {vl:.4f} ({deadline - time.time():.0f}s left)")
            if vl < best_val:
                best_val = vl
                save({k: v.cpu() for k, v in model.state_dict().items()})

    if best_val == float("inf"):  # never reached an eval — save whatever we have
        save({k: v.cpu() for k, v in model.state_dict().items()})
    print(f"done: {step} steps, best probed val loss {best_val:.4f}")


if __name__ == "__main__":
    main()
