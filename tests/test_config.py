import pytest
import yaml

from braided.config import RunConfig, SearchConfig, TaskConfig


def make_task(**overrides) -> TaskConfig:
    base = dict(
        name="t",
        scorer_command="python score.py",
        heldout_scorer_command="python heldout_score.py",
        protected_paths=["score.py"],
        budget_seconds=10,
        metric_name="m",
        direction="max",
    )
    base.update(overrides)
    return TaskConfig(**base)


def test_roundtrip_through_yaml(tmp_path):
    cfg = RunConfig(
        task=make_task(),
        search=SearchConfig(strategy="tree", max_total_runs=30, ucb_c=2.0),
        seed=42,
        output_dir=str(tmp_path),
    )
    path = tmp_path / "run.yaml"
    cfg.save(path)
    loaded = RunConfig.load(path)
    assert loaded == cfg
    # and the file is plain yaml
    raw = yaml.safe_load(path.read_text())
    assert raw["search"]["strategy"] == "tree"


def test_direction_helpers():
    lo = make_task(direction="min")
    hi = make_task(direction="max")
    assert lo.better(1.0, 2.0) and not lo.better(2.0, 1.0)
    assert hi.better(2.0, 1.0) and not hi.better(1.0, 2.0)
    assert lo.improvement(1.0, 2.0) == pytest.approx(1.0)
    assert hi.improvement(2.0, 1.0) == pytest.approx(1.0)


def test_private_scorer_must_differ():
    with pytest.raises(ValueError):
        RunConfig(
            task=make_task(heldout_scorer_command="python score.py"),
            output_dir="x",
        )
