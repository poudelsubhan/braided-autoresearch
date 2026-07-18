"""graph.json export (the data half of task 5.4): nodes with scores/flags,
edges with parent/merge links. Consumed by the demo page or any viz."""

from __future__ import annotations

import json
from pathlib import Path

from braided.graph import Graph
from braided.ledger import Ledger
from braided.report.tree import replicated_shas


def export_graph_json(run_dir: str | Path) -> Path:
    run_dir = Path(run_dir)
    graph = Graph(run_dir / "repo")
    ledger = Ledger(run_dir)
    replicated = replicated_shas(ledger)

    nodes, edges = [], []
    for n in graph.nodes():
        nodes.append({
            "sha": n.sha,
            "short": n.sha[:8],
            "score": n.score,
            "branch": n.branch,
            "kind": n.kind,
            "rationale": (n.rationale or "")[:160],
            "replicated": n.sha in replicated,
            "merge": n.is_merge,
        })
        for p in n.parents:
            edges.append({"from": p, "to": n.sha, "merge": n.is_merge})

    out = run_dir / "graph.json"
    out.write_text(json.dumps({"nodes": nodes, "edges": edges}, indent=2))
    return out
