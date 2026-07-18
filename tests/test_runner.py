import sys
import textwrap

from braided.config import TaskConfig
from braided.runner import find_protected_violations, parse_score_output, run_scorer


def make_task(scorer_command: str, budget: float = 2.0) -> TaskConfig:
    return TaskConfig(
        name="fake",
        scorer_command=scorer_command,
        heldout_scorer_command="python nonexistent_heldout.py",
        budget_seconds=budget,
    )


def write_scorer(tmp_path, body: str) -> None:
    (tmp_path / "fake_score.py").write_text(textwrap.dedent(body))


def run(tmp_path, budget: float = 2.0):
    task = make_task(f"{sys.executable} fake_score.py", budget=budget)
    return run_scorer(tmp_path, task, tmp_path / "logs")


def test_ok_path_with_noise_on_stdout(tmp_path):
    write_scorer(
        tmp_path,
        """
        print("some log line")
        print('{"score": 12.5, "extra_metric": 3}')
        """,
    )
    result = run(tmp_path)
    assert result.ok and result.score == 12.5
    assert result.extra == {"extra_metric": 3}
    assert "log line" in open(result.stdout_path).read()


def test_crash(tmp_path):
    write_scorer(tmp_path, "raise SystemExit(1)")
    result = run(tmp_path)
    assert not result.ok and result.failure_kind == "crash"


def test_invalid_output(tmp_path):
    write_scorer(tmp_path, "print('no json here')")
    result = run(tmp_path)
    assert not result.ok and result.failure_kind == "invalid-output"


def test_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr("braided.runner.GRACE_SECONDS", 0.5)
    write_scorer(tmp_path, "import time; time.sleep(60)")
    result = run(tmp_path, budget=0.5)
    assert not result.ok and result.failure_kind == "timeout"


def test_parse_score_output_edge_cases():
    assert parse_score_output('{"score": 1}')[0] == 1.0
    assert parse_score_output('{"score": "abc"}')[0] is None
    assert parse_score_output("")[0] is None
    # last JSON line wins
    score, _, _ = parse_score_output('{"score": 1}\n{"score": 2}')
    assert score == 2.0


def test_protected_path_enforcement():
    protected = ["score.py", "heldout_score.py", "data/*.txt"]
    assert find_protected_violations(["score.py"], protected) == ["score.py"]
    assert find_protected_violations(["./score.py"], protected) == ["./score.py"]
    assert find_protected_violations(["data/val.txt"], protected) == ["data/val.txt"]
    assert find_protected_violations(["train.py", "pipeline.py"], protected) == []
    got = find_protected_violations(["train.py", "heldout_score.py"], protected)
    assert got == ["heldout_score.py"]
