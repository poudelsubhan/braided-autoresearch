# Bake-off: three strategies, same task, same total attempt budget, fixed seeds.
# The agent prepares; the human presses go:  make bakeoff
#
# Each run gets its own fresh experiment repo + baseline calibration, then the
# search runs to completion (resume-safe: rerun make on interruption).

BUDGET ?= 60
RUNS   := runs/bakeoff-greedy runs/bakeoff-tree runs/bakeoff-braided

bakeoff: $(RUNS)
	uv run braided report --final --runs $(RUNS) --out .
	uv run braided report --audit --runs $(RUNS) --out .

runs/bakeoff-%:
	uv run python scripts/make_bakeoff_config.py $* $(BUDGET)
	uv run braided run --config $@/run.yaml
	uv run braided heldout-sweep --run $@

.PHONY: bakeoff
