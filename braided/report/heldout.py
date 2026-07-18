"""Held-out evaluation: best-vs-baseline summary + per-node sweep (task 4.4).

The private scorer is invoked only here and in Engine.run_heldout; its command
and data path must never reach agent context — enforced by
assert_no_private_leak, called on every prompt the agents build.
"""

from __future__ import annotations

import json
from pathlib import Path

from braided.config import RunConfig
from braided.engine import Engine
from braided.ledger import Ledger, ReplicationTagEvent


def heldout_summary(run_dir: str | Path) -> dict:
    run_dir = Path(run_dir)
    cfg = RunConfig.load(run_dir / "run.yaml")
    engine = Engine(cfg)
    root = engine.graph.root()
    best_sha, best_public = engine.best_node()

    baseline_heldout = engine.run_heldout(root, "heldout-baseline")
    best_heldout = engine.run_heldout(best_sha, "heldout-best")
    engine.graph.checkout("main")  # leave the repo in a sane state

    summary = {
        "baseline_public": engine.baseline["mean"],
        "best_public": best_public,
        "best_sha": best_sha,
        "baseline_heldout": baseline_heldout,
        "best_heldout": best_heldout,
        "public_gain": engine.task.improvement(best_public, engine.baseline["mean"]),
        "heldout_gain": (
            engine.task.improvement(best_heldout, baseline_heldout)
            if baseline_heldout is not None and best_heldout is not None
            else None
        ),
    }
    (run_dir / "heldout.json").write_text(json.dumps(summary, indent=2))
    return summary


def heldout_sweep(run_dir: str | Path, status_fn=print) -> dict:
    """Run the private scorer on the root and EVERY accepted node (attempts
    and composed merges), recording public vs held-out score and whether the
    node belongs to a replicated class. The Phase 5 headline dataset.
    Incremental: nodes already swept are skipped on re-run."""
    run_dir = Path(run_dir)
    cfg = RunConfig.load(run_dir / "run.yaml")
    engine = Engine(cfg)
    ledger = Ledger(run_dir)

    out_path = run_dir / "heldout_nodes.json"
    rows: dict[str, dict] = {}
    if out_path.exists():
        rows = {r["sha"]: r for r in json.loads(out_path.read_text())["nodes"]}

    replicated_shas = set()
    for e in ledger.events():
        if isinstance(e, ReplicationTagEvent):
            replicated_shas.update(e.member_shas)

    root = engine.graph.root()
    targets = [(root, "baseline", engine.baseline["mean"])]
    for e in ledger.attempts():
        if e.result == "accepted" and e.sha:
            targets.append((e.sha, "attempt", e.score))
    for e in ledger.merge_attempts():
        if e.result == "compose" and e.sha:
            targets.append((e.sha, "merge", e.score))

    for sha, kind, public in targets:
        if sha in rows:
            continue
        heldout = engine.run_heldout(sha, f"heldout-{sha[:8]}")
        rows[sha] = {
            "sha": sha, "kind": kind, "public": public, "heldout": heldout,
            "replicated": sha in replicated_shas,
        }
        status_fn(f"heldout {sha[:8]} ({kind}): public={public:.4f} "
                  f"heldout={heldout if heldout is None else round(heldout, 4)}")
    engine.graph.checkout("main")

    result = {"task": cfg.task.name, "nodes": list(rows.values())}
    out_path.write_text(json.dumps(result, indent=2))
    return result
