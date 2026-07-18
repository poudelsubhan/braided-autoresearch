"""Fallback patch applier for LLM-written unified diffs.

`git apply` is strict in ways models routinely violate: wrong hunk counts
(fixable with --recount), wrong @@ line numbers, and — fatally — hunks without
trailing context, which git anchors to end-of-file. This applier ignores line
numbers entirely: each hunk's old side (context + deletions) is located by
exact content match in the target file and replaced by the new side. A hunk
that matches nowhere, or in more than one place, is an error.

Used only after `git apply` has failed; the caller still enforces
protected-path checks before any application path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


class FuzzyPatchError(RuntimeError):
    pass


@dataclass
class FileHunks:
    old_path: str | None  # None = file creation
    new_path: str | None  # None = file deletion
    hunks: list[tuple[list[str], list[str]]] = field(default_factory=list)  # (old, new) line lists


def parse_patch(patch: str) -> list[FileHunks]:
    files: list[FileHunks] = []
    cur: FileHunks | None = None
    in_hunk = False
    lines = patch.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- "):
            old = line[4:].split("\t")[0].strip()
            i += 1
            if i >= len(lines) or not lines[i].startswith("+++ "):
                raise FuzzyPatchError("malformed patch: '---' without '+++'")
            new = lines[i][4:].split("\t")[0].strip()
            cur = FileHunks(
                old_path=None if old == "/dev/null" else re.sub(r"^[ab]/", "", old),
                new_path=None if new == "/dev/null" else re.sub(r"^[ab]/", "", new),
            )
            files.append(cur)
            in_hunk = False
        elif line.startswith("@@"):
            if cur is None:
                raise FuzzyPatchError("hunk before file header")
            cur.hunks.append(([], []))
            in_hunk = True
        elif in_hunk and cur is not None and cur.hunks:
            old_h, new_h = cur.hunks[-1]
            if line.startswith(" ") or line == "":
                old_h.append(line[1:])
                new_h.append(line[1:])
            elif line.startswith("-"):
                old_h.append(line[1:])
            elif line.startswith("+"):
                new_h.append(line[1:])
            elif line.startswith("\\"):  # "\ No newline at end of file"
                pass
            else:
                in_hunk = False  # trailing junk after the hunk
        i += 1
    if not any(f.hunks or f.old_path is None for f in files):
        raise FuzzyPatchError("no hunks found in patch")
    return files


def _find_block(haystack: list[str], needle: list[str], start: int) -> list[int]:
    if not needle:
        return []
    return [
        i
        for i in range(start, len(haystack) - len(needle) + 1)
        if haystack[i : i + len(needle)] == needle
    ]


def apply_patch_text(root: str | Path, patch: str) -> list[str]:
    """Apply the patch under root. All-or-nothing: validates every hunk of
    every file before writing anything. Returns changed paths."""
    root = Path(root)
    files = parse_patch(patch)
    staged: list[tuple[Path, str | None]] = []  # (path, new_content | None=delete)

    for fh in files:
        if fh.old_path is None:  # new file
            content = "\n".join(new for _, new_lines in fh.hunks for new in new_lines)
            staged.append((root / fh.new_path, content + ("\n" if content else "")))
            continue
        path = root / fh.old_path
        if not path.exists():
            raise FuzzyPatchError(f"target file missing: {fh.old_path}")
        if fh.new_path is None:  # deletion
            staged.append((path, None))
            continue
        lines = path.read_text().split("\n")
        pos = 0
        for old_h, new_h in fh.hunks:
            # strip fully-blank leading/trailing context that models often bungle
            while old_h and new_h and old_h[0] == "" and new_h and new_h[0] == "":
                old_h, new_h = old_h[1:], new_h[1:]
            matches = _find_block(lines, old_h, pos)
            if not matches:
                anywhere = _find_block(lines, old_h, 0)
                if len(anywhere) == 1:
                    matches = anywhere  # out-of-order hunks; still unambiguous
                else:
                    raise FuzzyPatchError(
                        f"hunk not found in {fh.old_path} (context mismatch): "
                        f"{old_h[:2]!r}..."
                    )
            if len(matches) > 1:
                raise FuzzyPatchError(
                    f"ambiguous hunk in {fh.old_path}: matches at lines {matches}"
                )
            m = matches[0]
            lines = lines[:m] + new_h + lines[m + len(old_h):]
            pos = m + len(new_h)
        staged.append((path, "\n".join(lines)))

    changed = []
    for path, content in staged:
        if content is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        changed.append(str(path.relative_to(root)))
    return changed
