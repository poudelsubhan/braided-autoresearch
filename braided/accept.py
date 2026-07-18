"""Noise-calibrated acceptance.

Accept a change only if its improvement over the parent exceeds
`accept_noise_multiplier × baseline_std` (run-to-run std of the unmodified
baseline, measured at init-task time).

Tradeoff, deliberately configurable: too loose and the ratchet fills with
accepted noise (each "win" is a coin flip, and the lineage's reported score
drifts upward without real progress — especially poisonous for the Phase 4/5
replication analysis, which keys off accepted nodes). Too tight and real small
wins get rejected, starving strategies whose whole point is compounding many
small improvements. 1.5× std is the default: ~93% one-sided confidence under
roughly-normal noise, cheap to revisit per task via run.yaml.
"""

from __future__ import annotations

import json
from pathlib import Path

from braided.config import RunConfig, TaskConfig


def load_baseline(run_dir: str | Path) -> dict:
    return json.loads((Path(run_dir) / "baseline.json").read_text())


def accept_threshold(baseline_std: float, multiplier: float) -> float:
    return multiplier * baseline_std


def threshold_for_run(cfg: RunConfig) -> float:
    baseline = load_baseline(cfg.output_dir)
    return accept_threshold(baseline["std"], cfg.search.accept_noise_multiplier)


def is_accepted(task: TaskConfig, new_score: float, parent_score: float, threshold: float) -> bool:
    return task.improvement(new_score, parent_score) > threshold
