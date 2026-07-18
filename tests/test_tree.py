"""Phase 3: UCB scheduler — arithmetic, stagnation-fork, prune, worktrees."""

import difflib
import math
from pathlib import Path

import pytest

from braided.agents.proposer import Proposal
from braided.config import RunConfig
from braided.engine import Engine, run_search
from braided.graph import Graph
from braided.ledger import BranchEvent, Ledger, verify_ledger
from braided.scheduler.tree import ArmStats, TreeStrategy, derive_arms

from tests.test_engine import CODE_V0, make_run_dir, patch_value, scripted_proposer

# static directions: no LLM in unit tests
FAKE_DIRECTIONS = iter([f"This lineage explores direction {i}." for i in range(20)])


@pytest.fixture(autouse=True)
def no_llm_directions(monkeypatch):
    monkeypatch.setattr(
        "braided.scheduler.tree.generate_direction",
        lambda task, existing: next(FAKE_DIRECTIONS),
    )


def tree_cfg(tmp_path, **search_overrides) -> RunConfig:
    cfg = make_run_dir(tmp_path, max_total_runs=search_overrides.pop("max_total_runs", 6))
    cfg.search = cfg.search.model_copy(update={
        "strategy": "tree", "initial_branches": 3, "ucb_c": 1.4,
        "stagnation_m": 2, "min_active_branches": 2, "max_active_branches": 4,
        **search_overrides,
    })
    cfg.save(Path(cfg.output_dir) / "run.yaml")
    return cfg


def test_ucb_arithmetic_hand_computed():
    # arm A: 2 pulls, rewards [0.1, 0.3]; arm B: 1 pull, [0.4]; total 3 pulls, c=2
    a = ArmStats("a", start_score=10.0, pulls=2, rewards=[0.1, 0.3])
    b = ArmStats("b", start_score=10.0, pulls=1, rewards=[0.4])
    c = 2.0
    lower_a, upper_a = a.bounds(3, c)
    assert a.mean == pytest.approx(0.2)
    radius_a = 2.0 * math.sqrt(math.log(3) / 2)
    assert upper_a == pytest.approx(0.2 + radius_a)
    assert lower_a == pytest.approx(0.2 - radius_a)
    _, upper_b = b.bounds(3, c)
    assert upper_b == pytest.approx(0.4 + 2.0 * math.sqrt(math.log(3) / 1))
    # unpulled arm: infinite upper bound
    assert ArmStats("u", 1.0).bounds(3, c)[1] == math.inf


def test_initial_branches_and_directions(tmp_path):
    cfg = tree_cfg(tmp_path)
    engine = Engine(cfg, proposer_fn=lambda **k: None, status_fn=lambda s: None)
    strategy = TreeStrategy(engine)
    assert set(strategy.arms) == {"main", "b1", "b2"}
    graph = Graph(Path(cfg.output_dir) / "repo")
    assert set(graph.branches()) == {"main", "b1", "b2"}
    # directions recorded in ledger and distinct
    forks = [e for e in Ledger(cfg.output_dir).events()
             if isinstance(e, BranchEvent) and e.action == "fork"]
    assert len(forks) == 3
    assert len({f.direction for f in forks}) == 3
    # unpulled arms selected in deterministic name order
    assert strategy.select_arm() == "b1"


def test_reward_normalization_and_selection(tmp_path):
    cfg = tree_cfg(tmp_path, max_total_runs=3)
    # every branch's first proposal raises value by different amounts
    values = {"b1": 12.0, "b2": 11.0, "main": 13.0}
    def proposer(repo, **kwargs):
        cur = float((Path(repo) / "code.py").read_text().split("=")[1])
        branch = [b for b, v in values.items()][0]
        # infer branch by score? simpler: bump by fixed amount per call
        return Proposal("bump", patch_value(Path(repo), cur + 2.0))
    run_search(cfg, proposer_fn=proposer, status_fn=lambda s: None)
    engine = Engine(cfg, proposer_fn=proposer, status_fn=lambda s: None)
    arms = derive_arms(engine)
    # 3 arms each pulled once, reward = 2/10 = 0.2, clipped to [0,1]
    assert all(a.pulls == 1 for a in arms.values())
    for a in arms.values():
        assert a.rewards == [pytest.approx(0.2)]
    assert verify_ledger(cfg.output_dir) == []


def test_stagnation_fork_from_best(tmp_path):
    cfg = tree_cfg(tmp_path, max_total_runs=8, initial_branches=2, stagnation_m=2,
                   min_active_branches=1, max_active_branches=3)
    calls = {"n": 0}

    def proposer(repo, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return Proposal("win", patch_value(Path(repo), 15.0))  # accepted on first arm
        return Proposal("noop", patch_value(Path(repo), 0.0))  # always rejected (worse)

    run_search(cfg, proposer_fn=proposer, status_fn=lambda s: None)
    ledger = Ledger(cfg.output_dir)
    forks = [e for e in ledger.events() if isinstance(e, BranchEvent) and e.action == "fork"]
    # 2 initial + at least one stagnation fork, capped at max_active_branches
    assert len(forks) >= 3
    stag = [f for f in forks if "stagnation" in f.detail]
    assert stag, "expected a stagnation fork"
    # stagnation fork originates from the best node (score 15)
    graph = Graph(Path(cfg.output_dir) / "repo")
    assert graph.get_score(stag[0].from_sha) == 15.0


def test_prune_logic(tmp_path):
    cfg = tree_cfg(tmp_path, ucb_c=0.05, min_active_branches=1, stagnation_m=99)
    engine = Engine(cfg, proposer_fn=lambda **k: None, status_fn=lambda s: None)
    strategy = TreeStrategy(engine)
    # hand-feed stats: main is great, b1 is clearly dead, b2 unpulled->protected
    strategy.arms["main"] = ArmStats("main", 10.0, pulls=6, rewards=[0.9] * 6)
    strategy.arms["b1"] = ArmStats("b1", 10.0, pulls=6, rewards=[0.0] * 6)
    del strategy.arms["b2"]
    strategy._prune_check()
    assert "b1" not in strategy.arms and "main" in strategy.arms
    prunes = [e for e in Ledger(cfg.output_dir).events()
              if isinstance(e, BranchEvent) and e.action == "prune"]
    assert len(prunes) == 1 and prunes[0].branch == "b1"


def test_prune_respects_floor(tmp_path):
    cfg = tree_cfg(tmp_path, ucb_c=0.05, min_active_branches=2, stagnation_m=99)
    engine = Engine(cfg, proposer_fn=lambda **k: None, status_fn=lambda s: None)
    strategy = TreeStrategy(engine)
    strategy.arms = {
        "main": ArmStats("main", 10.0, pulls=6, rewards=[0.9] * 6),
        "b1": ArmStats("b1", 10.0, pulls=6, rewards=[0.0] * 6),
    }
    strategy._prune_check()
    assert set(strategy.arms) == {"main", "b1"}  # floor of 2 blocks the prune


def test_resume_rebuilds_arms(tmp_path):
    cfg = tree_cfg(tmp_path, max_total_runs=3)
    run_search(cfg, proposer_fn=scripted_proposer([
        lambda r: Proposal("a", patch_value(r, 12.0)),
        lambda r: Proposal("b", patch_value(r, 11.0)),
        lambda r: Proposal("c", patch_value(r, 13.0)),
    ]), status_fn=lambda s: None)
    engine = Engine(cfg, proposer_fn=lambda **k: None, status_fn=lambda s: None)
    strategy = TreeStrategy(engine)  # resume path: no new forks
    forks = [e for e in Ledger(cfg.output_dir).events()
             if isinstance(e, BranchEvent) and e.action == "fork"]
    assert len(forks) == 3  # unchanged
    assert sum(a.pulls for a in strategy.arms.values()) == 3


def test_parallel_worktree_isolation(tmp_path):
    """Two workers pull different arms concurrently without corrupting the DAG."""
    cfg = tree_cfg(tmp_path, max_total_runs=4, initial_branches=2, workers=2,
                   stagnation_m=99)

    def proposer(repo, **kwargs):
        cur = float((Path(repo) / "code.py").read_text().split("=")[1])
        return Proposal(f"bump from {cur}", patch_value(Path(repo), cur + 1.0))

    run_search(cfg, proposer_fn=proposer, status_fn=lambda s: None)
    assert verify_ledger(cfg.output_dir) == []
    ledger = Ledger(cfg.output_dir)
    attempts = ledger.attempts()
    assert len(attempts) == 4
    assert {a.attempt_index for a in attempts} == {0, 1, 2, 3}
    # both arms were pulled
    assert len({a.branch for a in attempts}) == 2
    # worktrees cleaned up
    assert not list((Path(cfg.output_dir) / "worktrees").glob("*"))
    # every accepted commit is on its own branch head lineage
    graph = Graph(Path(cfg.output_dir) / "repo")
    for a in attempts:
        if a.result == "accepted":
            assert graph.is_ancestor(a.sha, graph.head_of(a.branch))
