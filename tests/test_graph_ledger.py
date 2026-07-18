"""Phase 1: Graph API, ledger, context queries, tree view."""

import difflib
import subprocess

import pytest

from braided.context import failure_digest, rationale_trail, sibling_digest
from braided.graph import (
    Graph,
    PatchError,
    ProtectedPathViolation,
    patch_changed_paths,
)
from braided.ledger import (
    AttemptEvent,
    BranchEvent,
    Ledger,
    MergeAttemptEvent,
    ReplicationTagEvent,
    verify_ledger,
)
from braided.report.tree import render_tree


def make_patch(path: str, old: str, new: str) -> str:
    lines = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
    )
    return "".join(lines)


@pytest.fixture
def run_dir(tmp_path):
    """A minimal run dir: experiment repo with a baseline commit + empty ledger."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "code.py").write_text("x = 1\ny = 2\nz = 3\n")
    (repo / "score.py").write_text("print('protected')\n")
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
    graph.set_meta(root, {"branch": "main", "kind": "baseline", "rationale": "baseline"})
    return tmp_path


def read(repo, name):
    return (repo / name).read_text()


def test_patch_changed_paths():
    patch = make_patch("code.py", "x = 1\n", "x = 2\n")
    assert patch_changed_paths(patch) == ["code.py"]


def test_synthetic_three_branch_dag_with_merge(run_dir):
    """The Phase 1 acceptance script."""
    graph = Graph(run_dir / "repo")
    ledger = Ledger(run_dir, run_id="test-run")
    root = graph.root()

    # three branches from root; main already exists
    graph.create_branch(root, "b1")
    graph.create_branch(root, "b2")

    def attempt(branch, old, new, rationale, score, idx):
        base = read(run_dir / "repo", "code.py")
        assert base == old
        patch = make_patch("code.py", old, new)
        sha = graph.commit_patch(
            branch, patch, f"attempt {idx}", protected=["score.py"],
            score=score, rationale=rationale,
        )
        ledger.append(AttemptEvent(
            attempt_index=idx, branch=branch, parent_sha=graph.lineage(sha)[1],
            sha=sha, rationale=rationale, result="accepted", score=score,
        ))
        return sha

    graph.checkout("main")
    a1 = attempt("main", "x = 1\ny = 2\nz = 3\n", "x = 10\ny = 2\nz = 3\n", "raise x", 11.0, 0)
    graph.checkout("b1")
    b1 = attempt("b1", "x = 1\ny = 2\nz = 3\n", "x = 1\ny = 20\nz = 3\n", "raise y", 12.0, 1)
    graph.checkout("b2")
    b2 = attempt("b2", "x = 1\ny = 2\nz = 3\n", "x = 1\ny = 2\nz = 30\n", "raise z", 11.5, 2)

    # agent-mediated merge of main+b1: combined patch against common ancestor
    combined = make_patch("code.py", "x = 1\ny = 2\nz = 3\n", "x = 10\ny = 20\nz = 3\n")
    m = graph.merge_commit(
        a1, b1, combined, "merge main+b1", branch="m1", protected=["score.py"],
        score=13.0, rationale="combine raise-x and raise-y",
    )
    ledger.append(MergeAttemptEvent(
        attempt_index=3, parents=[a1, b1], base_sha=root, branch="m1", sha=m,
        rationale="combine raise-x and raise-y", result="compose",
        score=13.0, parent_scores=[11.0, 12.0],
    ))

    # topology checks
    node = {n.sha: n for n in graph.nodes()}[m]
    assert sorted(node.parents) == sorted([a1, b1]) and node.is_merge
    assert read(run_dir / "repo", "code.py") == "x = 10\ny = 20\nz = 3\n"

    assert graph.leaves() == {b2, m}
    assert graph.lineage(a1) == [a1, root]
    assert set(graph.lineage(m)) == {m, a1, root}  # first-parent path
    assert graph.is_ancestor(root, m) and graph.is_ancestor(b1, m)
    assert not graph.is_ancestor(b2, m)

    # verify-ledger passes
    assert verify_ledger(run_dir) == []

    # tree view renders topology + scores + rationales
    tree = render_tree(run_dir, color=False)
    assert "13.0000" in tree and "12.0000" in tree and "10.0000" in tree
    assert "combine raise-x and raise-y" in tree
    assert "raise y" in tree
    # git's graph drawing shows a merge diamond
    assert "|\\" in tree.replace(" ", "") or "|/" in tree.replace(" ", "")

    # context queries
    trail = rationale_trail(graph, "b1")
    assert "raise y" in trail and "12.0" in trail
    sib = sibling_digest(graph, ledger, "b1")
    assert "m1" in sib and "b2" in sib and "b1" not in sib.split("—")[0]

    ledger.append(AttemptEvent(
        attempt_index=4, branch="b2", parent_sha=b2, rationale="bad idea",
        result="failed", failure_kind="crash", detail="boom",
    ))
    fails = failure_digest(ledger, "b2")
    assert "bad idea" in fails and "crash" in fails


def test_verify_ledger_catches_missing_event(run_dir):
    graph = Graph(run_dir / "repo")
    patch = make_patch("code.py", "x = 1\ny = 2\nz = 3\n", "x = 5\ny = 2\nz = 3\n")
    graph.checkout("main")
    graph.commit_patch("main", patch, "sneaky", score=1.0, rationale="sneaky")
    problems = verify_ledger(run_dir)
    assert any("no accepted attempt event" in p for p in problems)


def test_verify_ledger_catches_missing_score(run_dir):
    graph = Graph(run_dir / "repo")
    ledger = Ledger(run_dir)
    patch = make_patch("code.py", "x = 1\ny = 2\nz = 3\n", "x = 5\ny = 2\nz = 3\n")
    graph.checkout("main")
    sha = graph.commit_patch("main", patch, "no score", rationale="r")
    ledger.append(AttemptEvent(
        attempt_index=0, branch="main", parent_sha=graph.root(), sha=sha,
        rationale="r", result="accepted", score=1.0,
    ))
    problems = verify_ledger(run_dir)
    assert any("no score note" in p for p in problems)


def test_protected_path_rejected(run_dir):
    graph = Graph(run_dir / "repo")
    patch = make_patch("score.py", "print('protected')\n", "print('hacked')\n")
    graph.checkout("main")
    with pytest.raises(ProtectedPathViolation):
        graph.commit_patch("main", patch, "hack", protected=["score.py"])
    # working tree untouched
    assert read(run_dir / "repo", "score.py") == "print('protected')\n"


def test_malformed_patch_raises(run_dir):
    graph = Graph(run_dir / "repo")
    graph.checkout("main")
    with pytest.raises(PatchError):
        graph.apply_patch("--- a/code.py\n+++ b/code.py\n@@ garbage @@\nnope\n")
    with pytest.raises(PatchError):
        graph.apply_patch("   ")


def test_ledger_survives_restart(run_dir):
    ledger = Ledger(run_dir, run_id="r1")
    ledger.append(AttemptEvent(
        attempt_index=0, branch="main", parent_sha="p", result="rejected", score=1.0,
    ))
    # re-open (process restart) and append more
    ledger2 = Ledger(run_dir, run_id="r1")
    ledger2.append(BranchEvent(branch="b1", action="fork", direction="try algos"))
    ledger2.append(ReplicationTagEvent(class_id="c1", member_shas=["x"], lineages=["b1"]))
    events = list(ledger2.events())
    assert len(events) == 3
    assert events[0].attempt_index == 0 and events[0].run_id == "r1"
    assert ledger2.next_attempt_index() == 1
