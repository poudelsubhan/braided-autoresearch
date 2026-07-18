"""Interaction map (task 5.3): change-class pairs → compose/interfere/unknown,
built from merge_attempt records + class assignments."""

from __future__ import annotations

import json
from pathlib import Path

from braided.graph import Graph
from braided.ledger import ClassAssignEvent, Ledger


def _classes_on_lineage(graph: Graph, assigns: dict[str, str], base: str, head: str) -> set[str]:
    classes = set()
    for sha in graph.lineage(head):
        if sha == base or graph.is_ancestor(sha, base):
            break
        if sha in assigns:
            classes.add(assigns[sha])
    return classes


def interaction_map(run_dirs: list[str | Path]) -> dict:
    """{(class_a, class_b) -> {compose: n, interfere: n}} plus raw records."""
    pair_stats: dict[tuple[str, str], dict[str, int]] = {}
    records = []
    for run_dir in run_dirs:
        run_dir = Path(run_dir)
        if not (run_dir / "repo").exists():
            continue
        graph = Graph(run_dir / "repo")
        ledger = Ledger(run_dir)
        assigns = {e.sha: e.class_id for e in ledger.events()
                   if isinstance(e, ClassAssignEvent)}
        for m in ledger.merge_attempts():
            if m.result == "fail" or len(m.parents) != 2:
                continue
            ca = _classes_on_lineage(graph, assigns, m.base_sha, m.parents[0])
            cb = _classes_on_lineage(graph, assigns, m.base_sha, m.parents[1])
            records.append({
                "run": run_dir.name, "result": m.result,
                "classes_a": sorted(ca), "classes_b": sorted(cb),
                "score": m.score, "parent_scores": m.parent_scores,
            })
            for a in ca:
                for b in cb:
                    key = tuple(sorted((a, b)))
                    stats = pair_stats.setdefault(key, {"compose": 0, "interfere": 0})
                    stats[m.result] += 1
    return {
        "pairs": {f"{a} × {b}": v for (a, b), v in sorted(pair_stats.items())},
        "records": records,
    }


def render_markdown(imap: dict) -> str:
    if not imap["pairs"]:
        return "_No merge attempts with classified parents recorded._"
    lines = ["| class pair | compose | interfere |", "|---|---|---|"]
    for pair, stats in imap["pairs"].items():
        lines.append(f"| {pair} | {stats['compose']} | {stats['interfere']} |")
    return "\n".join(lines)
