"""Strategy-comparison harness: best public score vs attempts consumed,
for ≥2 completed runs of the same task under the same total budget.
The bake-off instrument for Phase 5."""

from __future__ import annotations

import json
from pathlib import Path

from braided.config import RunConfig
from braided.ledger import Ledger


def run_curve(run_dir: str | Path) -> dict:
    """Per-run: strategy name + best-so-far score after each attempt."""
    run_dir = Path(run_dir)
    cfg = RunConfig.load(run_dir / "run.yaml")
    baseline = json.loads((run_dir / "baseline.json").read_text())
    best = baseline["mean"]
    curve = [best]
    ledger = Ledger(run_dir)
    # merge attempts consume budget like any pull — one curve point each
    events = sorted(ledger.attempts() + ledger.merge_attempts(),
                    key=lambda e: e.attempt_index)
    for e in events:
        if e.score is not None and cfg.task.better(e.score, best):
            best = e.score
        curve.append(best)
    return {
        "run": run_dir.name,
        "strategy": cfg.search.strategy,
        "task": cfg.task.name,
        "baseline": baseline["mean"],
        "curve": curve,
        "best": best,
        "attempts": len(curve) - 1,
    }


def compare(run_dirs: list[str | Path], out_path: str | Path | None = None) -> str:
    """Comparison table (returned as str) + matplotlib plot (saved if out_path)."""
    curves = [run_curve(d) for d in run_dirs]
    tasks = {c["task"] for c in curves}
    if len(tasks) > 1:
        raise ValueError(f"runs span different tasks: {tasks}")

    header = f"{'run':<28} {'strategy':<8} {'attempts':>8} {'baseline':>10} {'best':>10} {'gain %':>8}"
    lines = [header, "-" * len(header)]
    for c in curves:
        gain = (c["best"] - c["baseline"]) / abs(c["baseline"]) * 100
        lines.append(
            f"{c['run']:<28} {c['strategy']:<8} {c['attempts']:>8} "
            f"{c['baseline']:>10.4f} {c['best']:>10.4f} {gain:>+8.1f}"
        )
    table = "\n".join(lines)

    if out_path:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(8, 5))
        for c in curves:
            ax.plot(range(len(c["curve"])), c["curve"],
                    label=f"{c['strategy']} ({c['run']})", linewidth=2)
        ax.set_xlabel("attempts consumed")
        ax.set_ylabel("best public score")
        ax.set_title(f"strategy bake-off — {curves[0]['task']}")
        ax.legend()
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(out_path, dpi=150)
        plt.close(fig)
    return table
