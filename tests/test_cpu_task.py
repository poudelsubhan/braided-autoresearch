"""cpu-optimize template: correctness of the naive pipeline vs the reference,
and end-to-end init-task on a tiny workload."""

import json
import sys
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parent.parent / "tasks" / "cpu-optimize" / "template"
sys.path.insert(0, str(TEMPLATE))


def test_naive_pipeline_matches_reference():
    import pipeline
    import score_common

    docs = score_common.make_corpus(seed=7, n_docs=4, words_per_doc=120, vocab_size=300)
    expected = score_common.reference_top_ngrams(docs, n=2, k=20, stopwords=pipeline.STOPWORDS)
    got = pipeline.top_ngrams(docs, n=2, k=20)
    assert got == expected
    assert len(got) == 20
    # ordering: counts descending, ties broken by string ascending
    for (g1, c1), (g2, c2) in zip(got, got[1:]):
        assert c1 > c2 or (c1 == c2 and g1 < g2)


def test_corpus_generation_is_deterministic():
    import score_common

    a = score_common.make_corpus(seed=3, n_docs=2, words_per_doc=50, vocab_size=100)
    b = score_common.make_corpus(seed=3, n_docs=2, words_per_doc=50, vocab_size=100)
    c = score_common.make_corpus(seed=4, n_docs=2, words_per_doc=50, vocab_size=100)
    assert a == b
    assert a != c


@pytest.mark.slow
def test_init_task_cpu_optimize(tmp_path, monkeypatch):
    from braided.runner import run_scorer
    from braided.tasks import init_task

    # tiny workload so the test is fast
    monkeypatch.setenv("BRAIDED_CPU_DOCS", "4")
    monkeypatch.setenv("BRAIDED_CPU_HELDOUT_DOCS", "3")
    monkeypatch.setenv("BRAIDED_SCORE_REPEATS", "1")
    run_dir = init_task("cpu-optimize", runs_root=tmp_path, run_id="t1", calibrate=2)

    repo = run_dir / "repo"
    assert (repo / ".git").is_dir()
    assert (repo / "pipeline.py").exists() and (repo / "score.py").exists()

    baseline = json.loads((run_dir / "baseline.json").read_text())
    assert len(baseline["scores"]) == 2 and all(s > 0 for s in baseline["scores"])
    assert (run_dir / "run.yaml").exists()

    # held-out scorer also runs on the fresh repo
    from braided.config import RunConfig

    cfg = RunConfig.load(run_dir / "run.yaml")
    res = run_scorer(
        repo, cfg.task, run_dir / "logs", log_prefix="heldout-check",
        command=cfg.task.heldout_scorer_command,
    )
    assert res.ok and res.score > 0
