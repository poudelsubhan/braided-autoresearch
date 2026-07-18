"""Prepare one bake-off run dir: init-task + strategy-specific run.yaml.
Usage: make_bakeoff_config.py <greedy|tree|braided> <max_total_runs>"""

import sys
from pathlib import Path

from braided.config import RunConfig
from braided.tasks import init_task

strategy, budget = sys.argv[1], int(sys.argv[2])
run_id = f"bakeoff-{strategy}"
run_dir = Path("runs") / run_id
if run_dir.exists():
    print(f"{run_dir} already exists — resuming with existing config")
    sys.exit(0)

init_task("cpu-optimize", run_id=run_id, calibrate=3, seed=0)
cfg = RunConfig.load(run_dir / "run.yaml")
cfg.search.strategy = strategy
cfg.search.max_total_runs = budget
cfg.search.initial_branches = 3
cfg.search.merge_cadence = 10          # braided only reads this
cfg.search.replication_k = 2
cfg.seed = 0
cfg.save(run_dir / "run.yaml")
print(f"prepared {run_dir} ({strategy}, {budget} attempts)")
