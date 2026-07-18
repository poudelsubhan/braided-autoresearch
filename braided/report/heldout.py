"""Held-out evaluation of a finished run: baseline root vs best node.

Full per-node held-out sweeps land in Phase 4 (task 4.4); this covers the
Phase 2/3 deliverable "best-vs-baseline public and held-out scores".
"""

from __future__ import annotations

import json
from pathlib import Path

from braided.config import RunConfig
from braided.engine import Engine


def heldout_summary(run_dir: str | Path) -> dict:
    run_dir = Path(run_dir)
    cfg = RunConfig.load(run_dir / "run.yaml")
    engine = Engine(cfg)
    root = engine.graph.root()
    best_sha, best_public = engine.best_node()

    baseline_heldout = engine.run_heldout(root, "heldout-baseline")
    best_heldout = engine.run_heldout(best_sha, "heldout-best")
    engine.graph.checkout("main")  # leave the repo in a sane state

    summary = {
        "baseline_public": engine.baseline["mean"],
        "best_public": best_public,
        "best_sha": best_sha,
        "baseline_heldout": baseline_heldout,
        "best_heldout": best_heldout,
        "public_gain": engine.task.improvement(best_public, engine.baseline["mean"]),
        "heldout_gain": (
            engine.task.improvement(best_heldout, baseline_heldout)
            if baseline_heldout is not None and best_heldout is not None
            else None
        ),
    }
    (run_dir / "heldout.json").write_text(json.dumps(summary, indent=2))
    return summary
