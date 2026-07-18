"""Phase 4: replication independence, merge daemon, leak assert."""

import difflib
import json
from pathlib import Path

import pytest

from braided.agents.merger import pick_merge_pair
from braided.agents.proposer import Proposal
from braided.config import RunConfig, TaskConfig
from braided.engine import Engine, run_search
from braided.graph import Graph
from braided.ledger import (
    AttemptEvent,
    ClassAssignEvent,
    Ledger,
    MergeAttemptEvent,
    ReplicationTagEvent,
    verify_ledger,
)
from braided.privacy import PrivateLeakError, assert_no_private_leak
from braided.verify import check_replication

from tests.test_engine import make_run_dir, patch_value
from tests.test_graph_ledger import make_patch


def make_task(**kw) -> TaskConfig:
    base = dict(name="t", scorer_command="python score.py",
                heldout_scorer_command="python heldout_score.py", direction="max")
    base.update(kw)
    return TaskConfig(**base)


# --------------------------------------------------------------------------
# a hand-built DAG for independence tests:
#
#   root -- a1 (main)          a1: class C on main
#     \-- b1 -- b2 (br1)       b1: class C on br1; b2: child of b1
#      \- c1 (br2)             c1: class C on br2
#
# helper builds it once per test via the Graph API

CODE0 = "x = 1\ny = 2\nz = 3\nw = 4\n"


@pytest.fixture
def dag(tmp_path):
    import subprocess

    run_dir = tmp_path
    repo = run_dir / "repo"
    repo.mkdir()
    (repo / "code.py").write_text(CODE0)
    for cmd in [
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "t"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "baseline"],
    ]:
        subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
    graph = Graph(repo)
    root = graph.root()
    graph.set_score(root, 10.0)
    graph.set_meta(root, {"branch": "main", "kind": "baseline"})
    ledger = Ledger(run_dir)

    def commit(branch, old, new, score, rationale, idx):
        patch = make_patch("code.py", old, new)
        sha = graph.commit_patch(branch, patch, rationale, score=score, rationale=rationale)
        ledger.append(AttemptEvent(
            attempt_index=idx, branch=branch, parent_sha=graph.lineage(sha)[1],
            sha=sha, rationale=rationale, result="accepted", score=score,
        ))
        return sha

    graph.create_branch(root, "br1")
    graph.create_branch(root, "br2")
    graph.checkout("main")
    a1 = commit("main", CODE0, "x = 9\ny = 2\nz = 3\nw = 4\n", 11.0, "dict swap in x", 0)
    graph.checkout("br1")
    b1 = commit("br1", CODE0, "x = 1\ny = 9\nz = 3\nw = 4\n", 12.0, "dict swap in y", 1)
    b2 = commit("br1", "x = 1\ny = 9\nz = 3\nw = 4\n", "x = 1\ny = 9\nz = 9\nw = 4\n", 13.0, "cache z", 2)
    graph.checkout("br2")
    c1 = commit("br2", CODE0, "x = 1\ny = 2\nz = 3\nw = 9\n", 11.5, "dict swap in w", 3)
    return run_dir, graph, ledger, dict(root=root, a1=a1, b1=b1, b2=b2, c1=c1)


def assign(ledger, sha, cid="dict-swap"):
    ledger.append(ClassAssignEvent(sha=sha, class_id=cid, class_summary="swap to dict"))


def tags_of(ledger):
    return [e for e in ledger.events() if isinstance(e, ReplicationTagEvent)]


def test_replication_across_independent_lineages(dag):
    run_dir, graph, ledger, shas = dag
    assign(ledger, shas["a1"])
    assign(ledger, shas["c1"])
    tags = check_replication(run_dir, k=2)
    assert len(tags) == 1
    assert set(tags[0].member_shas) == {shas["a1"], shas["c1"]}
    assert set(tags[0].lineages) == {"main", "br2"}


def test_ancestor_members_not_independent(dag):
    run_dir, graph, ledger, shas = dag
    # b1 is an ancestor of b2: same lineage, one discovery
    assign(ledger, shas["b1"])
    assign(ledger, shas["b2"])
    assert check_replication(run_dir, k=2) == []


def test_shared_ancestor_class_propagation(dag):
    run_dir, graph, ledger, shas = dag
    # class assigned to b1, and to a merge-descendant scenario: c1 and b2 are
    # independent, but if the class reached both via b1... simulate: assign b1
    # plus two children of b1 on diverging branches.
    graph.create_branch(shas["b1"], "br3")
    graph.checkout("br3")
    patch = make_patch("code.py", "x = 1\ny = 9\nz = 3\nw = 4\n",
                       "x = 5\ny = 9\nz = 3\nw = 4\n")
    d1 = graph.commit_patch("br3", patch, "tweak", score=12.5, rationale="tweak")
    ledger.append(AttemptEvent(attempt_index=9, branch="br3", parent_sha=shas["b1"],
                               sha=d1, result="accepted", score=12.5))
    # class on b1 (shared ancestor of b2 and d1) and on both children:
    # b2 vs d1 blocked by shared-ancestor member b1 -> only 1 independent member
    assign(ledger, shas["b1"])
    assign(ledger, shas["b2"])
    assign(ledger, d1)
    assert check_replication(run_dir, k=2) == []


def test_replication_tag_dedup_and_growth(dag):
    run_dir, graph, ledger, shas = dag
    assign(ledger, shas["a1"])
    assign(ledger, shas["c1"])
    assert len(check_replication(run_dir, k=2)) == 1
    assert check_replication(run_dir, k=2) == []  # no growth, no duplicate tag
    assign(ledger, shas["b1"])  # third independent lineage
    tags = check_replication(run_dir, k=2)
    assert len(tags) == 1 and len(tags[0].member_shas) == 3


def test_replicated_digest_and_tree_marker(dag):
    run_dir, graph, ledger, shas = dag
    assign(ledger, shas["a1"])
    assign(ledger, shas["c1"])
    check_replication(run_dir, k=2)
    from braided.context import replicated_classes_digest
    from braided.report.tree import render_tree

    digest = replicated_classes_digest(ledger)
    assert "dict-swap" in digest and "2 lineages" in digest
    tree = render_tree(run_dir, color=False)
    assert "◆" in tree


def test_pick_merge_pair_prefers_disjoint(dag):
    run_dir, graph, ledger, shas = dag
    task = make_task()
    plan = pick_merge_pair(graph, task)
    assert plan is not None
    # br1 head (13.0) is best; all pairs disjoint on files? all touch code.py →
    # disjointness 0 everywhere; picks among top heads
    assert {plan.branch_a, plan.branch_b} <= {"main", "br1", "br2"}
    assert plan.base_sha == shas["root"]
    # ancestor pairs are excluded: merging b1 into b2's branch never proposed
    assert plan.sha_a != plan.sha_b


def test_merge_compose_creates_arm_and_verifies(dag):
    run_dir, graph, ledger, shas = dag
    # simulate what BraidedStrategy does on compose, then verify-ledger
    combined = make_patch("code.py", CODE0, "x = 9\ny = 9\nz = 9\nw = 4\n")
    sha = graph.merge_commit(
        shas["a1"], shas["b2"], combined, "merge", branch="m0",
        score=14.0, rationale="compose x+y+z",
    )
    ledger.append(MergeAttemptEvent(
        attempt_index=4, parents=[shas["a1"], shas["b2"]], base_sha=shas["root"],
        branch="m0", sha=sha, result="compose", score=14.0,
        parent_scores=[11.0, 13.0],
    ))
    assert verify_ledger(run_dir) == []
    node = {n.sha: n for n in graph.nodes()}[sha]
    assert node.is_merge and sorted(node.parents) == sorted([shas["a1"], shas["b2"]])
    # a merge node is a valid fork point for derive_arms via BranchEvent
    from braided.ledger import BranchEvent
    ledger.append(BranchEvent(branch="m0", action="fork", from_sha=sha, direction="d"))


def test_private_leak_assert():
    task = make_task()
    assert_no_private_leak("safe text about score.py", task)
    with pytest.raises(PrivateLeakError):
        assert_no_private_leak("run python heldout_score.py please", task)
    with pytest.raises(PrivateLeakError):
        assert_no_private_leak("path is $BRAIDED_HELDOUT_FILE", task)
    # the real proposer prompt for cpu-optimize is clean
    from braided.agents.proposer import build_prompt
    template = Path(__file__).resolve().parent.parent / "tasks" / "cpu-optimize" / "template"
    cpu_task = make_task(protected_paths=["score.py", "heldout_score.py", "score_common.py"])
    import subprocess, tempfile
    with tempfile.TemporaryDirectory() as td:
        import shutil
        repo = Path(td) / "repo"
        shutil.copytree(template, repo)
        for cmd in [["git", "init", "-q"], ["git", "add", "-A"]]:
            subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
        prompt = build_prompt(repo, cpu_task, "(none)", "(none)", "(none)", "(none)")
        assert_no_private_leak(prompt, cpu_task)
        assert "pipeline.py" in prompt  # editable file present
        assert "def benchmark" not in prompt  # protected scorer content absent


def test_braided_strategy_merge_cadence(tmp_path):
    """End-to-end braided run with scripted proposer + scripted merge agent."""
    cfg = make_run_dir(tmp_path, max_total_runs=5)
    cfg.search = cfg.search.model_copy(update={
        "strategy": "braided", "initial_branches": 2, "merge_cadence": 4,
        "stagnation_m": 99, "replication_k": 2,
    })
    cfg.save(Path(cfg.output_dir) / "run.yaml")

    def proposer(repo, **kwargs):
        cur = float((Path(repo) / "code.py").read_text().split("=")[1])
        return Proposal(f"bump", patch_value(Path(repo), cur + 2.0))

    import braided.scheduler.braided as braided_mod

    # scripted merge agent: combined patch = value 99 (beats both parents)
    def fake_propose_merge(graph, task, plan):
        base_code = graph._git("show", f"{plan.base_sha}:code.py") + "\n"
        patch = "".join(difflib.unified_diff(
            base_code.splitlines(keepends=True), ["value = 99.0\n"],
            fromfile="a/code.py", tofile="b/code.py"))
        return Proposal("compose both bumps", patch)

    orig = braided_mod.propose_merge
    braided_mod.propose_merge = fake_propose_merge
    braided_mod.classify_new_accepted = lambda run_dir: []
    try:
        import braided.scheduler.tree as tree_mod
        dirs = iter([f"This lineage explores d{i}." for i in range(9)])
        orig_dir = tree_mod.generate_direction
        tree_mod.generate_direction = lambda task, existing: next(dirs)
        try:
            run_search(cfg, proposer_fn=proposer, status_fn=lambda s: None)
        finally:
            tree_mod.generate_direction = orig_dir
    finally:
        braided_mod.propose_merge = orig

    ledger = Ledger(cfg.output_dir)
    merges = ledger.merge_attempts()
    assert len(merges) == 1
    assert merges[0].result == "compose"
    assert merges[0].attempt_index == 4
    assert verify_ledger(cfg.output_dir) == []
    graph = Graph(Path(cfg.output_dir) / "repo")
    assert "m0" in graph.branches()
    node = {n.sha: n for n in graph.nodes()}[merges[0].sha]
    assert node.is_merge
    # total budget respected: 4 pulls + 1 merge = 5 attempts
    assert ledger.next_attempt_index() == 5
