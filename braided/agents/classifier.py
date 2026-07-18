"""Patch classifier: LLM judge assigning each accepted attempt a change-class
label from an open but stable vocabulary.

Prompted for consistency: sees previously assigned labels + exemplar
rationales and must reuse a class when the mechanism matches. Output is
groundwork for the replication verifier — persisted as class_assign events.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from braided.agents import llm
from braided.graph import Graph
from braided.ledger import AttemptEvent, ClassAssignEvent, Ledger

SYSTEM = """\
You classify code-change attempts from an automated optimization loop into
change-classes. A change-class names the MECHANISM of the change, not its
location: two patches that both "replace linear scan with dict lookup" share a
class even if they touch different functions.

Rules:
- Reuse an existing class whenever the mechanism matches; only mint a new
  class when no existing one fits.
- Class ids are short kebab-case slugs, e.g. `data-structure-swap`,
  `lr-schedule`, `caching`, `loop-fusion`, `string-building`, `top-k-heap`.
- Respond with a single JSON object, nothing else:
  {"class_id": "<slug>", "class_summary": "<one clause naming the mechanism>"}
"""

PROMPT = """\
# Existing classes (reuse when the mechanism matches)
{existing}

# Attempt to classify
Rationale: {rationale}

Diff (truncated):
```
{diff}
```

JSON only.
"""


class ClassifierError(RuntimeError):
    pass


def existing_classes(ledger: Ledger) -> dict[str, str]:
    """class_id -> latest summary, in first-seen order."""
    classes: dict[str, str] = {}
    for e in ledger.events():
        if isinstance(e, ClassAssignEvent):
            classes[e.class_id] = e.class_summary
    return classes


def assigned_shas(ledger: Ledger) -> set[str]:
    return {e.sha for e in ledger.events() if isinstance(e, ClassAssignEvent)}


def classify_attempt(ledger: Ledger, graph: Graph, event: AttemptEvent,
                     diff: str) -> ClassAssignEvent:
    classes = existing_classes(ledger)
    existing = (
        "\n".join(f"- {cid}: {summary}" for cid, summary in classes.items())
        or "(none yet — you are minting the first class)"
    )
    raw = llm.complete(
        PROMPT.format(existing=existing, rationale=event.rationale, diff=diff[:4000]),
        system=SYSTEM,
        max_tokens=300,
    )
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        raise ClassifierError(f"no JSON in classifier response: {raw[:200]!r}")
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise ClassifierError(f"classifier JSON invalid: {e}") from e
    class_id = re.sub(r"[^a-z0-9-]", "-", str(obj.get("class_id", "")).strip().lower())
    if not class_id:
        raise ClassifierError("classifier returned empty class_id")
    assign = ClassAssignEvent(
        sha=event.sha,
        class_id=class_id,
        class_summary=str(obj.get("class_summary", ""))[:200],
    )
    ledger.append(assign)
    return assign


def classify_new_accepted(run_dir: str | Path) -> list[ClassAssignEvent]:
    """Classify every accepted attempt that doesn't have a class yet."""
    run_dir = Path(run_dir)
    ledger = Ledger(run_dir)
    graph = Graph(run_dir / "repo")
    done = assigned_shas(ledger)
    out = []
    for e in ledger.attempts():
        if e.result != "accepted" or not e.sha or e.sha in done:
            continue
        diff = graph.diff_from(e.parent_sha, e.sha)
        try:
            out.append(classify_attempt(ledger, graph, e, diff))
        except (ClassifierError, llm.LLMError):
            continue  # classify on a later sweep; never blocks the loop
    return out
