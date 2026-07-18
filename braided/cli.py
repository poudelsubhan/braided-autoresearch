"""CLI: braided init-task | run | report | verify-ledger."""

from __future__ import annotations

import argparse
import json
import sys


def cmd_init_task(args: argparse.Namespace) -> int:
    from braided.tasks import init_task

    run_dir = init_task(
        args.task,
        runs_root=args.runs_root,
        run_id=args.run_id,
        calibrate=args.calibrate,
        budget_seconds=args.budget,
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
    print("`braided run` lands in Phase 2.", file=sys.stderr)
    return 2


def cmd_report(args: argparse.Namespace) -> int:
    print("`braided report` lands in Phase 1+.", file=sys.stderr)
    return 2


def cmd_verify_ledger(args: argparse.Namespace) -> int:
    print("`braided verify-ledger` lands in Phase 1.", file=sys.stderr)
    return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="braided", description="git-DAG autoresearch harness")
    sub = p.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-task", help="create a fresh experiment repo for a task")
    p_init.add_argument("task", help="task name (cpu-optimize | nanogpt-shakespeare)")
    p_init.add_argument("--runs-root", default="runs")
    p_init.add_argument("--run-id", default=None)
    p_init.add_argument("--calibrate", type=int, default=3, help="baseline scorer repetitions")
    p_init.add_argument("--budget", type=float, default=None, help="override budget_seconds")
    p_init.add_argument("--seed", type=int, default=0)
    p_init.set_defaults(func=cmd_init_task)

    p_run = sub.add_parser("run", help="drive a search strategy over an experiment repo")
    p_run.add_argument("--config", required=True, help="path to run.yaml")
    p_run.add_argument("--tui", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_report = sub.add_parser("report", help="inspect/render a run")
    p_report.add_argument("--run", help="run dir")
    p_report.add_argument("--tree", action="store_true")
    p_report.add_argument("--json", action="store_true")
    p_report.add_argument("--final", action="store_true")
    p_report.add_argument("--audit", action="store_true")
    p_report.set_defaults(func=cmd_report)

    p_verify = sub.add_parser("verify-ledger", help="cross-check ledger against the git DAG")
    p_verify.add_argument("--run", required=True, help="run dir")
    p_verify.set_defaults(func=cmd_verify_ledger)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
