"""Graph API: the experiment repo as a decorated DAG.

Git provides topology (commits, branches, true two-parent merge commits).
Decorations ride in notes namespaces:
  refs/notes/score — float score per accepted node
  refs/notes/meta  — JSON per node: {branch, rationale, kind: baseline|attempt|merge}

All operations are subprocess git; no textual git merges are ever performed —
merge nodes get an agent-written tree applied onto the common ancestor.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

SCORE_NS = "refs/notes/score"
META_NS = "refs/notes/meta"


class GitError(RuntimeError):
    pass


class PatchError(GitError):
    """Patch failed to apply."""


class ProtectedPathViolation(GitError):
    def __init__(self, paths: list[str]):
        super().__init__(f"patch touches protected paths: {paths}")
        self.paths = paths


def git(repo: str | Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed in {repo}: {proc.stderr.strip()}")
    return proc.stdout.rstrip("\n")


def patch_changed_paths(patch: str) -> list[str]:
    """Repo-relative paths a unified diff touches (old and new sides)."""
    paths = set()
    for line in patch.splitlines():
        m = re.match(r"^(?:---|\+\+\+) (?:([ab])/)?(.+)$", line)
        if m and m.group(2) != "/dev/null":
            paths.add(m.group(2).strip())
        m = re.match(r"^diff --git a/(.+?) b/(.+)$", line)
        if m:
            paths.add(m.group(1))
            paths.add(m.group(2))
        m = re.match(r"^rename (?:from|to) (.+)$", line)
        if m:
            paths.add(m.group(1))
    return sorted(paths)


@dataclass
class Node:
    sha: str
    parents: list[str]
    branch: str | None
    score: float | None
    rationale: str | None = None
    kind: str = "attempt"

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1


class Graph:
    def __init__(self, repo: str | Path):
        self.repo = Path(repo)
        if not (self.repo / ".git").exists():
            raise GitError(f"{self.repo} is not a git repository")

    def _git(self, *args: str, check: bool = True) -> str:
        return git(self.repo, *args, check=check)

    # -- notes ----------------------------------------------------------------

    def set_score(self, sha: str, score: float) -> None:
        self._git("notes", "--ref", SCORE_NS, "add", "-f", "-m", repr(float(score)), sha)

    def get_score(self, sha: str) -> float | None:
        out = self._git("notes", "--ref", SCORE_NS, "show", sha, check=False)
        try:
            return float(out)
        except ValueError:
            return None

    def set_meta(self, sha: str, meta: dict) -> None:
        self._git("notes", "--ref", META_NS, "add", "-f", "-m", json.dumps(meta), sha)

    def get_meta(self, sha: str) -> dict:
        out = self._git("notes", "--ref", META_NS, "show", sha, check=False)
        try:
            return json.loads(out)
        except (json.JSONDecodeError, ValueError):
            return {}

    # -- queries --------------------------------------------------------------

    def root(self) -> str:
        out = self._git("rev-list", "--max-parents=0", "--branches")
        roots = out.split()
        if len(roots) != 1:
            raise GitError(f"expected exactly one root commit, found {roots}")
        return roots[0]

    def nodes(self) -> list[Node]:
        out = self._git("log", "--branches", "--format=%H %P")
        result = []
        for line in out.splitlines():
            parts = line.split()
            sha, parents = parts[0], parts[1:]
            meta = self.get_meta(sha)
            result.append(
                Node(
                    sha=sha,
                    parents=parents,
                    branch=meta.get("branch"),
                    score=self.get_score(sha),
                    rationale=meta.get("rationale"),
                    kind=meta.get("kind", "attempt"),
                )
            )
        return result

    def leaves(self) -> set[str]:
        all_shas, has_child = set(), set()
        for line in self._git("log", "--branches", "--format=%H %P").splitlines():
            parts = line.split()
            all_shas.add(parts[0])
            has_child.update(parts[1:])
        return all_shas - has_child

    def branches(self) -> dict[str, str]:
        """branch name -> head sha"""
        out = self._git("for-each-ref", "refs/heads", "--format=%(refname:short) %(objectname)")
        return dict(line.split() for line in out.splitlines() if line)

    def lineage(self, sha: str) -> list[str]:
        """First-parent path from sha back to root (sha first)."""
        return self._git("rev-list", "--first-parent", sha).splitlines()

    def is_ancestor(self, a: str, b: str) -> bool:
        """True if a is an ancestor of b (full DAG, not first-parent)."""
        proc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", a, b], cwd=self.repo, capture_output=True
        )
        return proc.returncode == 0

    def merge_base(self, a: str, b: str) -> str:
        return self._git("merge-base", a, b)

    def head_of(self, branch: str) -> str:
        return self._git("rev-parse", branch)

    def current_branch(self) -> str | None:
        out = self._git("rev-parse", "--abbrev-ref", "HEAD", check=False)
        return None if out in ("", "HEAD") else out

    def diff_from(self, base_sha: str, sha: str) -> str:
        return self._git("diff", base_sha, sha)

    # -- mutations ------------------------------------------------------------

    def create_branch(self, from_sha: str, name: str) -> str:
        self._git("branch", name, from_sha)
        return name

    def checkout(self, ref: str) -> None:
        self._git("checkout", "-q", "-f", ref)
        self._git("clean", "-qfd")

    def apply_patch(self, patch: str, protected: list[str] | None = None) -> list[str]:
        """Apply a unified diff to the current working tree (no commit).
        Raises ProtectedPathViolation / PatchError. Returns changed paths."""
        from braided.runner import find_protected_violations

        if not patch.strip():
            raise PatchError("empty patch")
        changed = patch_changed_paths(patch)
        if protected:
            violations = find_protected_violations(changed, protected)
            if violations:
                raise ProtectedPathViolation(violations)
        with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False) as f:
            f.write(patch if patch.endswith("\n") else patch + "\n")
            patch_file = f.name
        proc = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", patch_file],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise PatchError(f"git apply failed: {proc.stderr.strip()[:800]}")
        return changed

    def commit_all(self, message: str, branch: str, score: float | None = None,
                   rationale: str | None = None, kind: str = "attempt") -> str:
        """Commit the current working tree onto `branch` (must be checked out)."""
        cur = self.current_branch()
        if cur != branch:
            raise GitError(f"expected to be on branch {branch}, on {cur!r}")
        self._git("add", "-A")
        self._git("commit", "-q", "--allow-empty", "-m", message)
        sha = self._git("rev-parse", "HEAD")
        meta = {"branch": branch, "kind": kind}
        if rationale is not None:
            meta["rationale"] = rationale
        self.set_meta(sha, meta)
        if score is not None:
            self.set_score(sha, score)
        return sha

    def commit_patch(self, branch: str, patch: str, message: str,
                     protected: list[str] | None = None,
                     score: float | None = None, rationale: str | None = None) -> str:
        """checkout branch head, apply patch, commit. Returns new sha."""
        self.checkout(branch)
        self.apply_patch(patch, protected=protected)
        return self.commit_all(message, branch, score=score, rationale=rationale)

    def merge_commit(self, sha_a: str, sha_b: str, patch: str, message: str,
                     branch: str, protected: list[str] | None = None,
                     score: float | None = None, rationale: str | None = None) -> str:
        """True two-parent merge commit whose tree is the agent-written combined
        patch applied to the common ancestor — never git's textual merge.
        Creates `branch` pointing at the new merge node and checks it out."""
        base = self.merge_base(sha_a, sha_b)
        self.checkout(base)  # detached
        self.apply_patch(patch, protected=protected)
        self._git("add", "-A")
        tree = self._git("write-tree")
        sha = self._git(
            "commit-tree", tree, "-p", sha_a, "-p", sha_b, "-m", message
        )
        self.create_branch(sha, branch)
        self.checkout(branch)
        meta = {"branch": branch, "kind": "merge"}
        if rationale is not None:
            meta["rationale"] = rationale
        self.set_meta(sha, meta)
        if score is not None:
            self.set_score(sha, score)
        return sha
