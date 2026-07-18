"""Live two-panel TUI (Rich): left = decorated DAG + per-arm UCB stats,
right = streaming attempt log.

Forge pattern: the TUI reads ONLY the ledger and graph (public artifacts),
so running with --tui changes nothing about the search itself. The engine's
status callback just triggers a refresh.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from braided.report.tree import render_tree


def _arm_table(engine) -> Table:
    from braided.scheduler.tree import derive_arms

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    for col in ("arm", "pulls", "mean", "upper"):
        table.add_column(col, justify="right" if col != "arm" else "left")
    arms = derive_arms(engine)
    total = max(sum(a.pulls for a in arms.values()), 1)
    c = engine.cfg.search.ucb_c
    for name in sorted(arms):
        a = arms[name]
        lower, upper = a.bounds(total, c)
        table.add_row(
            name, str(a.pulls), f"{a.mean:.4f}",
            "∞" if upper == float("inf") else f"{upper:.4f}",
        )
    return table


class TUI:
    def __init__(self, engine, max_log_lines: int = 30):
        self.engine = engine
        self.log: deque[str] = deque(maxlen=max_log_lines)
        self.console = Console()
        self.live: Live | None = None

    def _render(self):
        tree_text = Text.from_ansi(render_tree(self.engine.run_dir))
        left = Panel(
            Group(tree_text, Text(), _arm_table(self.engine)),
            title="experiment DAG", border_style="dim",
        )
        log_text = Text()
        for line in self.log:
            style = (
                "green" if "accepted" in line
                else "red" if "failed" in line
                else "yellow" if "rejected" in line
                else "cyan" if ("fork" in line or "merge" in line or "prune" in line)
                else ""
            )
            log_text.append(line + "\n", style=style)
        right = Panel(log_text, title="attempts", border_style="dim")
        layout = Layout()
        layout.split_row(Layout(left, ratio=3), Layout(right, ratio=2))
        return layout

    def __enter__(self):
        self.live = Live(self._render(), console=self.console,
                         refresh_per_second=2, screen=False)
        self.live.__enter__()
        return self

    def __exit__(self, *exc):
        if self.live:
            self.live.__exit__(*exc)

    def status(self, line: str) -> None:
        self.log.append(line)
        if self.live:
            self.live.update(self._render())
