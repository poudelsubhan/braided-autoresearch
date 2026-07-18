import pytest

from braided.patching import FuzzyPatchError, apply_patch_text

FILE = "def f():\n    a = 1\n    b = 2\n    return a + b\n\n\ndef g():\n    return 0\n"


@pytest.fixture
def root(tmp_path):
    (tmp_path / "m.py").write_text(FILE)
    return tmp_path


def test_no_trailing_context_and_wrong_numbers(root):
    # the exact failure mode git apply rejects: deletion at hunk end, no
    # trailing context, bogus line numbers
    patch = (
        "--- a/m.py\n+++ b/m.py\n@@ -99,4 +99,3 @@\n"
        " def f():\n-    a = 1\n-    b = 2\n+    a, b = 1, 2\n"
    )
    apply_patch_text(root, patch)
    assert (root / "m.py").read_text() == (
        "def f():\n    a, b = 1, 2\n    return a + b\n\n\ndef g():\n    return 0\n"
    )


def test_multiple_hunks_and_files(root):
    (root / "n.py").write_text("x = 1\n")
    patch = (
        "--- a/m.py\n+++ b/m.py\n"
        "@@ -1,2 +1,2 @@\n def f():\n-    a = 1\n+    a = 10\n"
        "@@ -7,2 +7,2 @@\n def g():\n-    return 0\n+    return 1\n"
        "--- a/n.py\n+++ b/n.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
    )
    apply_patch_text(root, patch)
    text = (root / "m.py").read_text()
    assert "a = 10" in text and "return 1" in text
    assert (root / "n.py").read_text() == "x = 2\n"


def test_new_file_creation(root):
    patch = "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1,2 @@\n+q = 1\n+w = 2\n"
    apply_patch_text(root, patch)
    assert (root / "new.py").read_text() == "q = 1\nw = 2\n"


def test_ambiguous_hunk_rejected(root):
    (root / "m.py").write_text("x = 1\ny = 2\nx = 1\ny = 2\n")
    patch = "--- a/m.py\n+++ b/m.py\n@@ -1,2 +1,2 @@\n x = 1\n-y = 2\n+y = 3\n"
    with pytest.raises(FuzzyPatchError, match="ambiguous"):
        apply_patch_text(root, patch)


def test_context_mismatch_rejected(root):
    patch = "--- a/m.py\n+++ b/m.py\n@@ -1,2 +1,2 @@\n def f():\n-    a = 999\n+    a = 1\n"
    with pytest.raises(FuzzyPatchError, match="not found"):
        apply_patch_text(root, patch)
    assert (root / "m.py").read_text() == FILE  # untouched


def test_all_or_nothing(root):
    # first file's hunk fine, second file's hunk bad -> nothing written
    (root / "n.py").write_text("x = 1\n")
    patch = (
        "--- a/m.py\n+++ b/m.py\n@@ -1,2 +1,2 @@\n def f():\n-    a = 1\n+    a = 10\n"
        "--- a/n.py\n+++ b/n.py\n@@ -1 +1 @@\n-x = 999\n+x = 2\n"
    )
    with pytest.raises(FuzzyPatchError):
        apply_patch_text(root, patch)
    assert (root / "m.py").read_text() == FILE
    assert (root / "n.py").read_text() == "x = 1\n"


def test_graph_apply_falls_back_to_fuzzy(tmp_path):
    import subprocess

    from braided.graph import Graph

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.py").write_text(FILE)
    for cmd in [
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "t"],
        ["git", "add", "-A"],
        ["git", "commit", "-qm", "x"],
    ]:
        subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
    graph = Graph(repo)
    # patch git apply rejects (no trailing context) but fuzzy accepts
    patch = (
        "--- a/m.py\n+++ b/m.py\n@@ -1,3 +1,2 @@\n"
        " def f():\n-    a = 1\n-    b = 2\n+    a, b = 1, 2\n"
    )
    graph.apply_patch(patch)
    assert "a, b = 1, 2" in (repo / "m.py").read_text()
