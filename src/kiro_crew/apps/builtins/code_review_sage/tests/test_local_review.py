"""Tests for the local review diff and finding contract."""
from __future__ import annotations

import subprocess

import pytest
from sage_lib import local_review


def _git(repo, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "example.py").write_text("value = 1\n", encoding="utf-8")
    _git(repo, "add", "example.py")
    _git(repo, "commit", "-qm", "initial")
    return repo


@pytest.fixture(autouse=True)
def _stub_sandboxed_git(monkeypatch):
    """Keep these parser tests independent of the runner's OS sandbox backend."""
    monkeypatch.setattr(
        local_review,
        "sandboxed_spawn_argv",
        lambda argv, *, env=None, **_: (argv, env or {}, None),
    )


def test_working_tree_diff_anchors_added_lines(tmp_path):
    repo = _repo(tmp_path)
    (repo / "example.py").write_text("value = 1\nvalue += 1\n", encoding="utf-8")

    diff = local_review.working_tree_diff(repo)

    assert diff.base_revision
    assert diff.revision.startswith(diff.base_revision + " + working-tree:")
    assert [item.path for item in diff.files] == ["example.py"]
    assert diff.files[0].changed_lines() == {2}
    assert diff.files[0].hunks[0].lines[-1].content == "value += 1"


def test_untracked_file_uses_repository_relative_path(tmp_path):
    repo = _repo(tmp_path)
    (repo / "new file.py").write_text("value = 2\n", encoding="utf-8")

    diff = local_review.working_tree_diff(repo)

    assert [item.path for item in diff.files] == ["new file.py"]
    assert diff.files[0].status == "added"
    assert diff.files[0].changed_lines() == {1}


def test_validate_finding_rejects_context_and_external_lines(tmp_path):
    repo = _repo(tmp_path)
    (repo / "example.py").write_text("value = 1\nvalue += 1\n", encoding="utf-8")
    diff = local_review.working_tree_diff(repo)

    with pytest.raises(ValueError, match="changed line"):
        local_review.validate_finding(
            {"file": "example.py", "line": 1, "severity": "warning",
             "title": "bad", "message": "not changed"}, diff, "session")

    with pytest.raises(ValueError, match="outside"):
        local_review.validate_finding(
            {"file": "secret.txt", "line": 1, "severity": "warning",
             "title": "bad", "message": "outside"}, diff, "session")


def test_validate_finding_accepts_deleted_line_and_rejects_invalid_old_line(tmp_path):
    repo = _repo(tmp_path)
    (repo / "example.py").write_text("value = 2\n", encoding="utf-8")
    diff = local_review.working_tree_diff(repo)

    finding = local_review.validate_finding(
        {"file": "example.py", "side": "old", "line": 1, "severity": "warning",
         "title": "old value", "message": "the old value is unsafe"}, diff, "session")
    assert finding.side == "old"

    with pytest.raises(ValueError, match="changed line"):
        local_review.validate_finding(
            {"file": "example.py", "side": "old", "line": 999, "severity": "warning",
             "title": "bad", "message": "not deleted"}, diff, "session")


def test_reconcile_preserves_dismissal_and_marks_missing_findings_resolved(tmp_path):
    repo = _repo(tmp_path)
    (repo / "example.py").write_text("value = 1\nvalue += 1\n", encoding="utf-8")
    diff = local_review.working_tree_diff(repo)
    old = local_review.validate_finding(
        {"file": "example.py", "line": 2, "severity": "warning", "category": "correctness",
         "title": "duplicate", "message": "same issue"}, diff, "session")
    old.status = "dismissed"
    old.user_instruction = "Keep the public API."
    current = local_review.validate_finding(
        {"file": "example.py", "line": 2, "severity": "warning", "category": "correctness",
         "title": "changed title", "message": "same issue"}, diff, "session")

    result = local_review.reconcile_findings([old], [current])
    assert result[0].status == "dismissed"
    assert result[0].user_instruction == "Keep the public API."

    missing = local_review.validate_finding(
        {"file": "example.py", "line": 2, "severity": "warning", "category": "correctness",
         "title": "gone", "message": "gone issue"}, diff, "session")
    result = local_review.reconcile_findings([missing], [])
    assert missing.status == "resolved"
    assert result == [missing]


def test_context_is_bounded(tmp_path):
    repo = _repo(tmp_path)
    (repo / "example.py").write_text("x = 'a' * 100000\n", encoding="utf-8")
    diff = local_review.working_tree_diff(repo)

    assert len(local_review.build_context(diff).encode("utf-8")) <= local_review.MAX_CONTEXT_BYTES
