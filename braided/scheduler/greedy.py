"""Greedy baseline: single lineage `main`, keep-or-revert. The control
condition for the Phase 5 bake-off."""

from __future__ import annotations


class GreedyStrategy:
    def __init__(self, engine):
        self.engine = engine

    def step(self) -> None:
        self.engine.execute_attempt("main")
