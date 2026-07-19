"""CLI: braided init-task | run | report | verify-ledger."""

from __future__ import annotations

import argparse
import json
import sys


def cmd_init_task(args: argparse.Namespace) -> int:
    from braided.tasks import init_task

    from braided.config import SearchConfig

    search = SearchConfig(
        strategy=args.strategy,
        merge_cadence=args.merge_cadence,
        max_total_runs=args.attempts,
    )
    run_dir = init_task(
        args.task,
        runs_root=args.runs_root,
        run_id=args.run_id,
        calibrate=args.calibrate,
        budget_seconds=args.budget,
        search=search,
        seed=args.seed,
    )
    baseline = json.loads((run_dir / "baseline.json").read_text())
    print(f"initialized {run_dir}")
    print(
        f"baseline over {len(baseline['scores'])} run(s): "
        f"mean={baseline['mean']:.4f} std={baseline['std']:.4f} scores={baseline['scores']}"
    )
    print(f"config: {run_dir / 'run.yaml'}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from braided.config import RunConfig
    from braided.engine import run_search

    cfg = RunConfig.load(args.config)
    # line-flushing status so `run.log` tails work when stdout is redirected
    run_search(cfg, tui=args.tui, status_fn=lambda s: print(s, flush=True))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    if args.tree:
        if not args.run:
            print("--tree requires --run <run-dir>", file=sys.stderr)
            return 2
        from braided.report.tree import render_tree

        print(render_tree(args.run))
        return 0
    if args.json:
        from braided.report.graph_html import write_graph_html
        from braided.report.graphjson import export_graph_json

        path = export_graph_json(args.run)
        html = write_graph_html(args.run)
        print(f"wrote {path} and {html} — open via `python -m http.server` in the run dir")
        return 0
    if args.final:
        from braided.report.final import build_final_report

        path = build_final_report(args.runs or [args.run], out_dir=args.out)
        print(f"wrote {path}")
        return 0
    if args.audit:
        from braided.report.final import audit

        problems = audit(args.runs or [args.run],
                         report_path=str(args.out) + "/REPORT.md" if args.out != "." else "REPORT.md")
        if problems:
            for p in problems:
                print(f"AUDIT FAIL: {p}")
            return 1
        print("report audit clean: every figure traces to ledger data")
        return 0
    print("choose one of --tree/--json/--final/--audit", file=sys.stderr)
    return 2


def cmd_heldout_sweep(args: argparse.Namespace) -> int:
    from braided.report.heldout import heldout_summary, heldout_sweep

    heldout_sweep(args.run)
    summary = heldout_summary(args.run)
    print(json.dumps(summary, indent=2))
    return 0


def cmd_verify_ledger(args: argparse.Namespace) -> int:
    from braided.ledger import verify_ledger

    problems = verify_ledger(args.run)
    if problems:
        for p in problems:
            print(f"FAIL: {p}")
        return 1
    print("ledger and DAG are consistent")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="braided", description="git-DAG autoresearch harness")
    sub = p.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-task", help="create a fresh experiment repo for a task")
    p_init.add_argument("task", help="task name (cpu-optimize | nanogpt-shakespeare)")
    p_init.add_argument("--runs-root", default="runs")
    p_init.add_argument("--run-id", default=None)
    p_init.add_argument("--calibrate", type=int, default=3, help="baseline scorer repetitions")
    p_init.add_argument("--budget", type=float, default=None, help="override budget_seconds")
    p_init.add_argument("--strategy", choices=["greedy", "tree", "braided"], default="greedy")
    p_init.add_argument("--merge-cadence", type=int, default=10)
    p_init.add_argument("--attempts", type=int, default=20, help="max_total_runs")
    p_init.add_argument("--seed", type=int, default=0)
    p_init.set_defaults(func=cmd_init_task)

    p_run = sub.add_parser("run", help="drive a search strategy over an experiment repo")
    p_run.add_argument("--config", required=True, help="path to run.yaml")
    p_run.add_argument("--tui", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_report = sub.add_parser("report", help="inspect/render a run")
    p_report.add_argument("--run", help="run dir")
    p_report.add_argument("--runs", nargs="*", help="run dirs (for --final/--audit)")
    p_report.add_argument("--out", default=".", help="output dir for --final")
    p_report.add_argument("--tree", action="store_true")
    p_report.add_argument("--json", action="store_true")
    p_report.add_argument("--final", action="store_true")
    p_report.add_argument("--audit", action="store_true")
    p_report.set_defaults(func=cmd_report)

    p_sweep = sub.add_parser("heldout-sweep",
                             help="run the private scorer on every accepted node")
    p_sweep.add_argument("--run", required=True)
    p_sweep.set_defaults(func=cmd_heldout_sweep)

    p_verify = sub.add_parser("verify-ledger", help="cross-check ledger against the git DAG")
    p_verify.add_argument("--run", required=True, help="run dir")
    p_verify.set_defaults(func=cmd_verify_ledger)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
