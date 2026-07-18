"""Config schemas. A run is fully described by one run.yaml (RunConfig)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class TaskConfig(BaseModel):
    """A benchmark task the loop operates on.

    Commands run with cwd = the experiment repo working tree and must print a
    single JSON object {"score": <float>} on their last stdout line.
    """

    name: str
    entry_command: str | None = None  # optional human entrypoint, not used by the loop
    scorer_command: str  # public scorer; the optimization target
    heldout_scorer_command: str  # private scorer; never shown to agents
    protected_paths: list[str] = Field(default_factory=list)  # globs, repo-relative
    budget_seconds: float = 180.0  # wall-clock budget the scorer enforces internally
    metric_name: str = "score"
    direction: Literal["min", "max"] = "max"  # is a lower or higher score better?

    def better(self, a: float, b: float) -> bool:
        """True if score a is strictly better than score b."""
        return a < b if self.direction == "min" else a > b

    def improvement(self, new: float, old: float) -> float:
        """Signed improvement of new over old, positive = better."""
        return old - new if self.direction == "min" else new - old


class SearchConfig(BaseModel):
    strategy: Literal["greedy", "tree", "braided"] = "greedy"
    max_total_runs: int = 20  # total scorer executions across all branches
    ucb_c: float = 1.4  # UCB1 exploration constant
    merge_cadence: int = 10  # attempt a merge every N attempts (braided only)
    replication_k: int = 2  # class replicated iff accepted in >= k independent lineages
    initial_branches: int = 3  # tree/braided: arms at start
    stagnation_m: int = 4  # fork a new branch after m consecutive rejections on an arm
    min_active_branches: int = 2  # prune floor
    max_active_branches: int = 4  # stagnation forks stop above this cap
    workers: int = 1  # parallel arm pulls (cpu-bound tasks only)
    accept_noise_multiplier: float = 1.5  # accept iff improvement > mult * baseline std


class RunConfig(BaseModel):
    task: TaskConfig
    search: SearchConfig = Field(default_factory=SearchConfig)
    seed: int = 0
    output_dir: str  # the run dir: ledger, logs, and repo/ live here

    @model_validator(mode="after")
    def _no_private_leak(self) -> "RunConfig":
        # The private scorer command must never coincide with anything agents see.
        if self.task.heldout_scorer_command == self.task.scorer_command:
            raise ValueError("heldout_scorer_command must differ from scorer_command")
        return self

    @classmethod
    def load(cls, path: str | Path) -> "RunConfig":
        with open(path) as f:
            return cls.model_validate(yaml.safe_load(f))

    def save(self, path: str | Path) -> None:
        with open(path, "w") as f:
            yaml.safe_dump(self.model_dump(), f, sort_keys=False)


def load_task_config(path: str | Path) -> TaskConfig:
    with open(path) as f:
        return TaskConfig.model_validate(yaml.safe_load(f))
