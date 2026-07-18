"""Phase 2: engine attempt cycle, acceptance threshold, resume, patch rejection."""

import difflib
import json
from pathlib import Path

import pytest

from braided.accept import accept_threshold, is_accepted
from braided.agents.proposer import Proposal, ProposalError, parse_response
from braided.config import RunConfig, SearchConfig, TaskConfig
from braided.engine import Engine, run_search
from braided.graph import Graph
from braided.ledger import Ledger

# ---------------------------------------------------------------------------
# fixture: a run dir whose "scorer" reads a number out of the code file itself,
# so accepting/rejecting is fully scripted.

CODE_V0 = "value = 10.0\n"
SCORER = """\
import json, re
src = open("code.py").read()
value = float(re.search(r"value = ([0-9.]+)", src).group(1))
print(json.dumps({"score": value}))
"""


def make_run_dir(tmp_path, max_total_runs=5, std=0.1):
    import subprocess

    run_dir = tmp_path / "run"
    repo = run_dir / "repo"
    repo.mkdir(parents=True)
    (run_dir / "logs").mkdir()
    (repo / "code.py").write_text(CODE_V0)
    (repo / "score.py").write_text(SCORER)
    for cmd in [
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "t"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "baseline"],
    ]:
        subprocess.run(cmd, cwd=repo, check=True, capture_output=True)

    task = TaskConfig(
        name="scripted",
        scorer_command="python score.py",
        heldout_scorer_command="python score.py --heldout",
        protected_paths=["score.py"],
        budget_seconds=5,
        direction="max",
    )
    cfg = RunConfig(
        task=task,
        search=SearchConfig(strategy="greedy", max_total_runs=max_total_runs,
                            accept_noise_multiplier=1.5),
        output_dir=str(run_dir),
    )
    cfg.save(run_dir / "run.yaml")
    (run_dir / "baseline.json").write_text(json.dumps(
        {"task": "scripted", "scores": [10.0], "mean": 10.0, "std": std,
         "budget_seconds": 5}
    ))
    graph = Graph(repo)
    root = graph.root()
    graph.set_score(root, 10.0)
    graph.set_meta(root, {"branch": "main", "kind": "baseline", "rationale": "baseline"})
    return cfg


def patch_value(repo: Path, new_value: float) -> str:
    old = (repo / "code.py").read_text()
    new = f"value = {new_value}\n"
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile="a/code.py", tofile="b/code.py",
    ))


def scripted_proposer(script):
    """script: list of callables (repo -> Proposal) or exceptions to raise."""
    it = iter(script)

    def fn(repo, **kwargs):
        item = next(it)
        if isinstance(item, Exception):
            raise item
        return item(Path(repo))

    return fn


# ---------------------------------------------------------------------------


def test_accept_threshold_logic():
    task = TaskConfig(
        name="t", scorer_command="a", heldout_scorer_command="b", direction="max"
    )
    thr = accept_threshold(baseline_std=0.2, multiplier=1.5)
    assert thr == pytest.approx(0.3)
    assert is_accepted(task, 10.35, 10.0, thr)
    assert not is_accepted(task, 10.25, 10.0, thr)  # within noise
    assert not is_accepted(task, 9.0, 10.0, thr)
    task_min = task.model_copy(update={"direction": "min"})
    assert is_accepted(task_min, 9.65, 10.0, thr)
    assert not is_accepted(task_min, 9.75, 10.0, thr)


def test_greedy_accept_reject_and_failures(tmp_path):
    cfg = make_run_dir(tmp_path, max_total_runs=4)
    repo = Path(cfg.output_dir) / "repo"
    script = [
        lambda r: Proposal("raise value to 12", patch_value(r, 12.0)),   # accept
        lambda r: Proposal("raise value to 12.05", patch_value(r, 12.05)),  # within noise -> reject
        ProposalError("model returned garbage"),                          # bad-proposal
        lambda r: Proposal("hack the scorer", "--- a/score.py\n+++ b/score.py\n@@ -1 +1 @@\n-x\n+y\n"),
    ]
    lines = []
    run_search(cfg, proposer_fn=scripted_proposer(script), status_fn=lines.append)

    ledger = Ledger(cfg.output_dir)
    attempts = ledger.attempts()
    assert [a.result for a in attempts] == ["accepted", "rejected", "failed", "failed"]
    assert attempts[2].failure_kind == "bad-proposal"
    assert attempts[3].failure_kind == "protected-path-violation"
    # accepted commit landed; rejected attempt reverted the tree
    assert (repo / "code.py").read_text() == "value = 12.0\n"
    graph = Graph(repo)
    assert graph.get_score(graph.head_of("main")) == 12.0
    result = json.loads((Path(cfg.output_dir) / "result.json").read_text())
    assert result["best_score"] == 12.0
    from braided.ledger import verify_ledger

    assert verify_ledger(cfg.output_dir) == []


def test_patch_apply_failure_is_nonfatal(tmp_path):
    cfg = make_run_dir(tmp_path, max_total_runs=2)
    script = [
        lambda r: Proposal("malformed", "--- a/code.py\n+++ b/code.py\n@@ -1 +1 @@\n-not in file\n+x\n"),
        lambda r: Proposal("raise to 13", patch_value(r, 13.0)),
    ]
    run_search(cfg, proposer_fn=scripted_proposer(script), status_fn=lambda s: None)
    attempts = Ledger(cfg.output_dir).attempts()
    assert attempts[0].result == "failed" and attempts[0].failure_kind == "patch-apply-failed"
    assert attempts[1].result == "accepted"


def test_resume_from_interrupt(tmp_path):
    cfg = make_run_dir(tmp_path, max_total_runs=3)

    # first process: 2 attempts, then "interrupt" (engine simply stops)
    engine = Engine(cfg, proposer_fn=scripted_proposer([
        lambda r: Proposal("to 11", patch_value(r, 11.0)),
        lambda r: Proposal("to 11.05", patch_value(r, 11.05)),  # rejected
    ]), status_fn=lambda s: None)
    engine.execute_attempt("main")
    engine.execute_attempt("main")
    assert engine.attempts_done() == 2

    # second process: run_search picks up at attempt index 2 and does only one more
    lines = []
    run_search(cfg, proposer_fn=scripted_proposer([
        lambda r: Proposal("to 12", patch_value(r, 12.0)),
    ]), status_fn=lines.append)
    assert any("resuming: 2/3" in l for l in lines)
    attempts = Ledger(cfg.output_dir).attempts()
    assert len(attempts) == 3
    assert [a.attempt_index for a in attempts] == [0, 1, 2]
    graph = Graph(Path(cfg.output_dir) / "repo")
    assert graph.get_score(graph.head_of("main")) == 12.0


def test_scorer_crash_reverts_tree(tmp_path):
    cfg = make_run_dir(tmp_path, max_total_runs=1)
    repo = Path(cfg.output_dir) / "repo"
    # patch that makes the scorer regex fail -> crash
    script = [lambda r: Proposal("break it", "".join(difflib.unified_diff(
        CODE_V0.splitlines(keepends=True), ["value = broken\n"],
        fromfile="a/code.py", tofile="b/code.py")))]
    run_search(cfg, proposer_fn=scripted_proposer(script), status_fn=lambda s: None)
    attempts = Ledger(cfg.output_dir).attempts()
    assert attempts[0].result == "failed" and attempts[0].failure_kind == "crash"
    assert (repo / "code.py").read_text() == CODE_V0  # reverted


def test_parse_response_variants():
    p = parse_response('{"rationale": "r", "patch": "--- a/x\\n+++ b/x\\n"}')
    assert p.rationale == "r"
    p = parse_response('```json\n{"rationale": "r", "patch": "d"}\n```')
    assert p.patch == "d"
    p = parse_response('Here you go:\n{"rationale": "r", "patch": "d"} thanks')
    assert p.rationale == "r"
    with pytest.raises(ProposalError):
        parse_response("no json at all")
    with pytest.raises(ProposalError):
        parse_response('{"rationale": "only rationale"}')
