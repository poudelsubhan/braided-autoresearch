"""Direction hints: a persistent one-line exploration mandate per lineage.

Generated at branch birth so lineages stay genuinely different — without this,
every branch converges on the same obvious edits and Phase 4's replication
signal is worthless. Falls back to a static pool when the LLM is unavailable.
"""

from __future__ import annotations

from braided.agents import llm
from braided.config import TaskConfig

FALLBACK_POOL = [
    "This lineage explores algorithmic complexity: replace superlinear scans and loops with better algorithms.",
    "This lineage explores data-structure choices: dicts, sets, heaps, arrays in place of lists and linear search.",
    "This lineage explores micro-optimizations: string building, attribute lookups, comprehensions, builtins.",
    "This lineage explores caching and precomputation: memoize repeated work, hoist invariants out of loops.",
    "This lineage explores restructuring the main pipeline: fuse passes, stream instead of materialize, batch work.",
]

PROMPT = """\
You are naming an exploration direction for one lineage of an automated
code-optimization search on the task below. Existing lineages already cover
the listed directions; produce ONE new direction that is clearly distinct
from all of them, phrased as a single sentence starting with
"This lineage explores". It must be a family of mechanisms (not one specific
edit), plausible for the task, and disjoint from the existing directions.
Respond with that sentence only.

Task: {task_name} — metric {metric} ({better} is better), fixed wall-clock budget.

Existing directions:
{existing}
"""


def generate_direction(task: TaskConfig, existing: list[str]) -> str:
    try:
        text = llm.complete(
            PROMPT.format(
                task_name=task.name,
                metric=task.metric_name,
                better="lower" if task.direction == "min" else "higher",
                existing="\n".join(f"- {d}" for d in existing) or "(none)",
            ),
            max_tokens=200,
        ).strip()
        first_line = text.splitlines()[0].strip()
        if first_line:
            return first_line[:300]
    except llm.LLMError:
        pass
    for candidate in FALLBACK_POOL:
        if candidate not in existing:
            return candidate
    return FALLBACK_POOL[len(existing) % len(FALLBACK_POOL)]
