"""Replication-vs-generalization analysis (task 5.2).

Claim under test: replicated improvements retain more of their public-score
gain on the held-out scorer than unreplicated ones — i.e. cross-lineage
replication is a free reward-hacking detector.

Per accepted node (across all supplied runs): public gain vs held-out gain
relative to the node's parent, split by replicated/unreplicated. Output:
scatter plot + a simple effect estimate with n. Directional finding, not
p-value theater — sample sizes are honest and printed.
"""

from __future__ import annotations

import json
from pathlib import Path

from braided.config import RunConfig
from braided.graph import Graph
from braided.ledger import Ledger


def node_gains(run_dir: str | Path) -> list[dict]:
    """Rows of {sha, run, replicated, public_gain_frac, heldout_gain_frac}.

    Gains are fractional improvements of the node over its PARENT node
    (parent public score from git notes; parent held-out score from the
    heldout_nodes.json sweep), so each row isolates one accepted change."""
    run_dir = Path(run_dir)
    cfg = RunConfig.load(run_dir / "run.yaml")
    task = cfg.task
    sweep_path = run_dir / "heldout_nodes.json"
    if not sweep_path.exists():
        return []
    sweep = {r["sha"]: r for r in json.loads(sweep_path.read_text())["nodes"]}
    graph = Graph(run_dir / "repo")
    ledger = Ledger(run_dir)

    def parent_of(e) -> str | None:
        if hasattr(e, "parent_sha"):
            return e.parent_sha
        return None

    rows = []
    events = [e for e in ledger.attempts() if e.result == "accepted" and e.sha]
    for e in events:
        row = sweep.get(e.sha)
        parent_row = sweep.get(e.parent_sha) or (
            # parent may be the root: sweep stores it under kind=baseline
            next((r for r in sweep.values() if r["kind"] == "baseline"
                  and e.parent_sha == graph.root()), None)
        )
        if not row or not parent_row:
            continue
        if row["heldout"] is None or parent_row["heldout"] is None:
            continue
        pub = task.improvement(row["public"], parent_row["public"]) / abs(parent_row["public"])
        held = task.improvement(row["heldout"], parent_row["heldout"]) / abs(parent_row["heldout"])
        rows.append({
            "sha": e.sha, "run": run_dir.name, "replicated": row["replicated"],
            "public_gain_frac": pub, "heldout_gain_frac": held,
            "retention": held / pub if pub > 0 else None,
        })
    return rows


def analyze(run_dirs: list[str | Path], out_png: str | Path | None = None) -> dict:
    rows = [r for d in run_dirs for r in node_gains(d)]
    rep = [r for r in rows if r["replicated"]]
    unrep = [r for r in rows if not r["replicated"]]

    def mean_retention(group):
        vals = [r["retention"] for r in group if r["retention"] is not None]
        return (sum(vals) / len(vals), len(vals)) if vals else (None, 0)

    rep_ret, rep_n = mean_retention(rep)
    unrep_ret, unrep_n = mean_retention(unrep)
    result = {
        "n_total": len(rows),
        "replicated": {"n": rep_n, "mean_retention": rep_ret},
        "unreplicated": {"n": unrep_n, "mean_retention": unrep_ret},
        "effect": (rep_ret - unrep_ret) if rep_ret is not None and unrep_ret is not None else None,
        "rows": rows,
    }

    if out_png and rows:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(7, 6))
        for group, color, label in ((unrep, "#888888", "unreplicated"),
                                    (rep, "#d62728", "replicated ◆")):
            if group:
                ax.scatter([r["public_gain_frac"] for r in group],
                           [r["heldout_gain_frac"] for r in group],
                           c=color, label=f"{label} (n={len(group)})", s=60, alpha=0.8)
        lim = max([abs(r["public_gain_frac"]) for r in rows]
                  + [abs(r["heldout_gain_frac"]) for r in rows]) * 1.1
        ax.plot([0, lim], [0, lim], "k--", alpha=0.3, label="full retention")
        ax.axhline(0, color="k", linewidth=0.5)
        ax.set_xlabel("public-score gain (fraction of parent)")
        ax.set_ylabel("held-out gain (fraction of parent)")
        ax.set_title("replication vs generalization — per accepted change")
        ax.legend()
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(out_png, dpi=150)
        plt.close(fig)
    return result
