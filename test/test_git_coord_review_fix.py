from __future__ import annotations

import subprocess

import pytest

from kiro_crew.review_fix_git import (
    ReviewFixGitError,
    apply_patch,
    assert_target_unchanged,
    candidate_patch,
    commit_group,
    create_candidate,
    dirty_overlap,
    discard_candidate,
    inspect_target,
    write_patch,
)
from kiro_crew.task_models import ReviewFixTargetMode


def _git(repo, *args):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "feature/fix")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Review Fix Test")
    (repo / "target.txt").write_text("before\n", encoding="utf-8")
    (repo / "unrelated.txt").write_text("keep\n", encoding="utf-8")
    _git(repo, "add", "--", "target.txt", "unrelated.txt")
    _git(repo, "commit", "-m", "initial")
    return repo


@pytest.mark.asyncio
async def test_candidate_patch_apply_and_scoped_commit_preserve_unrelated_file(tmp_path):
    repo = _repo(tmp_path)
    target = await inspect_target(repo, mode=ReviewFixTargetMode.CURRENT_BRANCH)
    candidate = await create_candidate(
        target,
        tmp_path / "candidate",
        "kirocrew/review-fix/test-1",
    )
    candidate_file = tmp_path / "candidate" / "target.txt"
    candidate_file.write_text("after\n", encoding="utf-8")

    patch = await candidate_patch(candidate.candidate_worktree_path, target.head_sha)
    assert patch.paths == ("target.txt",)
    patch = await write_patch(patch, tmp_path / "patch.diff")
    assert patch.patch_id

    await apply_patch(target, patch)
    assert (repo / "target.txt").read_text(encoding="utf-8") == "after\n"
    assert (repo / "unrelated.txt").read_text(encoding="utf-8") == "keep\n"

    commit_sha = await commit_group(repo, patch.paths, "fix: apply review finding")
    assert commit_sha
    assert "unrelated.txt" not in _git(repo, "show", "--name-only", "--format=", "HEAD").stdout
    await discard_candidate(candidate, target.repo_root)


@pytest.mark.asyncio
async def test_target_fingerprint_and_path_overlap_are_conservative(tmp_path):
    repo = _repo(tmp_path)
    clean = await inspect_target(repo)
    (repo / "unrelated.txt").write_text("local change\n", encoding="utf-8")
    dirty = await inspect_target(repo)

    assert clean.dirty_fingerprint != dirty.dirty_fingerprint
    assert dirty_overlap(dirty, ["unrelated.txt"]) == ["unrelated.txt"]
    assert dirty_overlap(dirty, ["target.txt"]) == []
    with pytest.raises(ReviewFixGitError):
        assert_target_unchanged(clean, dirty)


@pytest.mark.asyncio
async def test_candidate_is_built_from_captured_head(tmp_path):
    repo = _repo(tmp_path)
    target = await inspect_target(repo)
    (repo / "target.txt").write_text("uncommitted target\n", encoding="utf-8")
    candidate = await create_candidate(target, tmp_path / "candidate", "kirocrew/review-fix/test-2")

    assert (tmp_path / "candidate" / "target.txt").read_text(encoding="utf-8") == "before\n"
    await discard_candidate(candidate, target.repo_root)
