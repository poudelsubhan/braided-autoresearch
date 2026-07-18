"""TUI renders headless from public artifacts only; compare harness works."""

from pathlib import Path

from braided.agents.proposer import Proposal
from braided.engine import Engine, run_search
from braided.report.compare import compare, run_curve

from tests.test_engine import make_run_dir, patch_value, scripted_proposer


def finished_run(tmp_path, name, scores):
    cfg = make_run_dir(tmp_path / name, max_total_runs=len(scores))
    script = [
        (lambda v: (lambda r: Proposal(f"to {v}", patch_value(r, v))))(v) for v in scores
    ]
    run_search(cfg, proposer_fn=scripted_proposer(script), status_fn=lambda s: None)
    return cfg


def test_tui_renders_headless(tmp_path):
    from rich.console import Console

    from braided.tui import TUI

    cfg = finished_run(tmp_path, "a", [12.0, 14.0])
    engine = Engine(cfg, proposer_fn=lambda **k: None, status_fn=lambda s: None)
    tui = TUI(engine)
    tui.log.append("[000] main accepted score=12")
    console = Console(record=True, width=140)
    console.print(tui._render())
    text = console.export_text()
    assert "experiment DAG" in text and "attempts" in text
    assert "arm" in text  # UCB stats table header


def test_compare_harness(tmp_path):
    a = finished_run(tmp_path, "a", [12.0, 14.0])
    b = finished_run(tmp_path, "b", [11.0, 16.0])
    curve = run_curve(a.output_dir)
    assert curve["curve"] == [10.0, 12.0, 14.0]
    out = tmp_path / "cmp.png"
    table = compare([a.output_dir, b.output_dir], out_path=out)
    assert "greedy" in table and out.exists()
