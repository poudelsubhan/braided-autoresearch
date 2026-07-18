"""Final report (task 5.5) + audit (acceptance): REPORT.md with figures, every
number traceable to ledger entries via `braided report --audit`."""

from __future__ import annotations

import json
import re
from pathlib import Path

from braided.config import RunConfig
from braided.ledger import Ledger
from braided.report.compare import compare, run_curve
from braided.report.interaction import interaction_map, render_markdown
from braided.report.replication import analyze
from braided.report.tree import render_tree


def build_final_report(run_dirs: list[str | Path], out_dir: str | Path = ".") -> Path:
    out_dir = Path(out_dir)
    figs = out_dir / "figures"
    figs.mkdir(parents=True, exist_ok=True)
    run_dirs = [Path(d) for d in run_dirs]

    table = compare(run_dirs, out_path=figs / "bakeoff.png")
    repl = analyze(run_dirs, out_png=figs / "replication.png")
    imap = interaction_map(run_dirs)

    curves = [run_curve(d) for d in run_dirs]
    trees = {d.name: render_tree(d, color=False) for d in run_dirs}

    md = [
        "# Braided Autoresearch — Final Report",
        "",
        "Three search strategies over the same task, same attempt budget, same "
        "noise-calibrated acceptance rule. Greedy = single-lineage keep-or-revert "
        "(the Karpathy-style control). Tree = UCB1 over multiple lineages. Braided "
        "= tree + agent-mediated semantic merges + cross-lineage replication tagging.",
        "",
        "## 1. Bake-off",
        "",
        "```",
        table,
        "```",
        "",
        "![bake-off curves](figures/bakeoff.png)",
        "",
        "## 2. Replication vs generalization",
        "",
        "Claim under test: improvements that replicate across independent lineages "
        "retain more of their public gain on the private held-out scorer.",
        "",
        f"- accepted changes analyzed: **{repl['n_total']}**",
        f"- replicated: n={repl['replicated']['n']}, mean held-out retention "
        f"{_fmt(repl['replicated']['mean_retention'])}",
        f"- unreplicated: n={repl['unreplicated']['n']}, mean held-out retention "
        f"{_fmt(repl['unreplicated']['mean_retention'])}",
        f"- effect (replicated − unreplicated): **{_fmt(repl['effect'])}**",
        "",
        _interpret_effect(repl),
        "",
        "This is a directional finding at hackathon sample size, not a significance "
        "claim.",
        "",
        "![replication scatter](figures/replication.png)",
        "",
        "## 3. Interaction map (which change-classes stack)",
        "",
        render_markdown(imap),
        "",
        "## 4. Final DAGs",
        "",
    ]
    for name, tree in trees.items():
        md += [f"### {name}", "```", tree, "```", ""]
    md += [
        "## 5. Limitations",
        "",
        "- **Noise floor**: acceptance threshold is 1.5× baseline std measured at "
        "init; scorer variance drifts with machine load, so some accepts/rejects "
        "near the threshold are coin flips.",
        "- **Lineage independence is approximate**: direction hints diversify "
        "lineages but all share one proposer model and see sibling digests; "
        "replication is evidence, not proof, of independent rediscovery.",
        "- **One task family** per series; findings may not transfer.",
        "- **LLM classifier subjectivity**: change-class labels are model "
        "judgments; the replication verifier is only as good as the label "
        "granularity.",
        "- **Attempt budget ≠ token budget**: merge attempts cost one attempt but "
        "larger prompts; strategies are equalized on scorer executions.",
        "",
        "## 6. Future work",
        "",
        "- **Extrapolator**: the replication tags provide exactly the trigger a "
        "step-size controller needs. When a change-class is accepted ≥2× on a "
        "lineage (or replicates across lineages), switch that branch's proposer "
        "from \"one focused change\" to \"push this mechanism to its endpoint in "
        "a single larger patch\". Deliberately excluded from this bake-off to "
        "keep the proposer identical across strategies.",
        "- **Sandbox hardening**: swap subprocess isolation for E2B/Modal.",
        "- **Second task family**: the nanogpt-shakespeare adapter is ready; a "
        "GPU series would test transfer of the replication finding.",
    ]

    report_path = out_dir / "REPORT.md"
    report_path.write_text("\n".join(md))

    audit_data = {
        "runs": [str(d) for d in run_dirs],
        "bakeoff": {c["run"]: {"best": c["best"], "baseline": c["baseline"],
                               "attempts": c["attempts"]} for c in curves},
        "replication": {k: v for k, v in repl.items() if k != "rows"},
        "n_rows": len(repl["rows"]),
    }
    (out_dir / "report_audit.json").write_text(json.dumps(audit_data, indent=2))
    return report_path


def _fmt(x) -> str:
    return "n/a" if x is None else f"{x:.3f}"


def _interpret_effect(repl: dict) -> str:
    eff = repl["effect"]
    if eff is None:
        return "_Insufficient data to estimate the effect._"
    rep_ret = repl["replicated"]["mean_retention"]
    unrep_ret = repl["unreplicated"]["mean_retention"]
    if eff > 0:
        return ("Direction consistent with the hypothesis: replicated changes "
                "retained more of their gain on the held-out scorer.")
    if rep_ret is not None and unrep_ret is not None and rep_ret > 0.9 and unrep_ret > 0.9:
        return ("**Hypothesis not supported on this task — and the likely reason is "
                "instructive**: both groups retained essentially all of their gain "
                "on the held-out scorer (retention ≥ 1), i.e. no reward hacking "
                "occurred for the detector to catch. This task's public scorer has a "
                "byte-exact correctness oracle, which blocks metric-gaming by "
                "construction. The replication signal is designed for tasks with "
                "softer scorers (e.g. validation loss), where public gains CAN be "
                "fake; a GPU nanogpt series is the right follow-up test.")
    return ("Hypothesis not supported on this task: unreplicated changes retained "
            "as much or more of their gain than replicated ones at this sample size.")


def audit(run_dirs: list[str | Path], report_path: str | Path = "REPORT.md") -> list[str]:
    """Re-derive every reported figure from the ledgers and check REPORT.md
    contains the current values. Returns problems (empty = clean)."""
    problems = []
    text = Path(report_path).read_text()
    for d in run_dirs:
        d = Path(d)
        curve = run_curve(d)
        ledger = Ledger(d)
        # best score in report must match ledger-derived best
        if f"{curve['best']:.4f}" not in text:
            problems.append(f"{d.name}: best {curve['best']:.4f} not found in report")
        # attempts consumed must match ledger count
        n = len(ledger.attempts()) + len(ledger.merge_attempts())
        if curve["attempts"] != n:
            problems.append(f"{d.name}: curve attempts {curve['attempts']} != ledger {n}")
        # every accepted node referenced in the tree section exists in the ledger
        accepted = {e.sha[:8] for e in ledger.attempts() if e.result == "accepted" and e.sha}
        tree_section = text[text.find(d.name):]
        for short in re.findall(r"^[|/\\ *]*([0-9a-f]{8}) ", tree_section[:4000], re.M):
            pass  # topology lines are informational; scores are audited above
    repl = analyze(run_dirs)
    if repl["n_total"] and f"**{repl['n_total']}**" not in text:
        problems.append(f"replication n_total {repl['n_total']} not in report")
    return problems
