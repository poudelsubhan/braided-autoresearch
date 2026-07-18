"""Private-scorer leak guard.

The held-out scorer command and held-out data path must never appear in any
string an agent sees. Called on every prompt the proposer/merger/classifier
builds. Raises — a leak is a bug, not a recoverable condition.
"""

from __future__ import annotations

from braided.config import TaskConfig


class PrivateLeakError(AssertionError):
    pass


def assert_no_private_leak(text: str, task: TaskConfig) -> str:
    """Returns text unchanged if clean; raises PrivateLeakError otherwise."""
    needles = [task.heldout_scorer_command, "BRAIDED_HELDOUT_FILE", "heldout.txt"]
    for needle in needles:
        if needle and needle in text:
            raise PrivateLeakError(
                f"private scorer material {needle!r} leaked into agent context"
            )
    return text
