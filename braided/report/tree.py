"""Decorated `git log --graph`: git renders topology, we append decorations.

green = accepted node, cyan = merge node, dim = node on a pruned branch,
◆ = member of a replicated class.
"""

from __future__ import annotations

import re
from pathlib import Path

from braided.graph import Graph
from braided.ledger import BranchEvent, Ledger, ReplicationTagEvent

GREEN, CYAN, DIM, RESET = "\x1b[32m", "\x1b[36m", "\x1b[2m", "\x1b[0m"


def pruned_branches(ledger: Ledger) -> set[str]:
    pruned = set()
    for e in ledger.events():
        if isinstance(e, BranchEvent):
            if e.action == "prune":
                pruned.add(e.branch)
            elif e.action == "fork":
                pruned.discard(e.branch)
    return pruned


def replicated_shas(ledger: Ledger) -> set[str]:
    shas = set()
    for e in ledger.events():
        if isinstance(e, ReplicationTagEvent):
            shas.update(e.member_shas)
    return shas


def render_tree(run_dir: str | Path, color: bool = True) -> str:
    run_dir = Path(run_dir)
    graph = Graph(run_dir / "repo")
    ledger = Ledger(run_dir)

    nodes = {n.sha: n for n in graph.nodes()}
    pruned = pruned_branches(ledger)
    replicated = replicated_shas(ledger)

    raw = graph._git("log", "--graph", "--branches", "--format=%H")
    out_lines = []
    for line in raw.splitlines():
        m = re.search(r"[0-9a-f]{40}", line)
        if not m:
            out_lines.append(line)
            continue
        sha = m.group(0)
        node = nodes.get(sha)
        prefix = line[: m.start()]
        short = sha[:8]
        score = f"{node.score:.4f}" if node and node.score is not None else "?"
        rationale = (node.rationale or "")[:50] if node else ""
        marker = "◆ " if sha in replicated else ""
        branch = node.branch if node else None
        branch_s = f" ({branch})" if branch else ""
        body = f"{short} {marker}[{score}]{branch_s} {rationale}".rstrip()
        if color:
            if branch in pruned:
                body = f"{DIM}{body}{RESET}"
            elif node and node.is_merge:
                body = f"{CYAN}{body}{RESET}"
            else:
                body = f"{GREEN}{body}{RESET}"
        out_lines.append(prefix + body)
    return "\n".join(out_lines)
