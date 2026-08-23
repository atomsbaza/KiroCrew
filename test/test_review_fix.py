from __future__ import annotations

from pathlib import Path
from test.test_git_coord_review_fix import _repo

import pytest

from kiro_crew.review_fix import (
    ReviewFixModelResolutionError,
    ReviewFixPlanError,
    build_review_fix_groups,
    create_review_fix_task,
    resolve_pinned_model,
    validate_group,
)
from kiro_crew.review_fix_git import discard_candidate
from kiro_crew.task_models import (
    ReviewFixDependencyGroup,
    ReviewFixFindingSnapshot,
    ReviewFixGitRecord,
    ReviewFixMetadata,
    ReviewFixState,
    ReviewFixTargetSnapshot,
)
from kiro_crew.taskrunner import TaskRunner


class _Sessions:
    _sessions: dict = {}


def test_resolve_pinned_model_requires_concrete_advertised_id():
    resolved = resolve_pinned_model("served-model", ["served-model"], provider="acp", resolved_at=2.0)
    assert resolved.resolved_model_id == "served-model"
    assert resolved.advertised_model_ids == ["served-model"]
    assert resolved.resolved_at == 2.0

    for requested, advertised in (("auto", ["served-model"]), ("served-model", []), ("other", ["served-model"])):
        with pytest.raises(ReviewFixModelResolutionError):
            resolve_pinned_model(requested, advertised)


def test_grouping_requires_exact_finding_coverage():
    findings = [
        ReviewFixFindingSnapshot(key="red", file_path="a.py"),
        ReviewFixFindingSnapshot(key="yellow", file_path="b.py"),
    ]
    groups = build_review_fix_groups(
        findings,
        [{"group_id": "hard-1", "finding_keys": ["red", "yellow"], "hard": True}],
    )
    assert groups[0].hard is True
    assert groups[0].affected_files == ["a.py", "b.py"]

    with pytest.raises(ReviewFixPlanError):
        build_review_fix_groups(findings, [{"finding_keys": ["red"]}])


@pytest.mark.asyncio
async def test_create_review_fix_task_persists_candidate_and_waits_for_group_confirmation(tmp_path):
    repo = _repo(tmp_path)
    runner = TaskRunner(_Sessions(), work_dir=tmp_path / "runner")
    run = await create_review_fix_task(
        runner,
        target_path=repo,
        findings=[{"key": "red", "title": "Fix target", "path": "target.txt", "body": "change it"}],
        review_run_id="sage-1",
        pr_url="https://github.com/example/repo/pull/1",
        requested_model="served-model",
        advertised_model_ids=["served-model"],
    )

    assert run.execution_mode == "review_fix"
    assert run.review_fix is not None
    assert run.review_fix.state is ReviewFixState.AWAITING_GROUP_CONFIRMATION
    assert run.review_fix.git.candidate_worktree_path
    assert (tmp_path / "repo" / "target.txt").read_text(encoding="utf-8") == "before\n"
    await discard_candidate(run.review_fix.git, run.review_fix.target.repo_root)


@pytest.mark.asyncio
async def test_create_review_fix_task_blocks_dirty_overlap(tmp_path):
    repo = _repo(tmp_path)
    (repo / "target.txt").write_text("local\n", encoding="utf-8")
    runner = TaskRunner(_Sessions(), work_dir=tmp_path / "runner")
    run = await create_review_fix_task(
        runner,
        target_path=repo,
        findings=[{"key": "red", "path": "target.txt", "body": "change it"}],
        requested_model="served-model",
        advertised_model_ids=["served-model"],
    )
    assert run.review_fix is not None
    assert run.review_fix.state is ReviewFixState.BLOCKED_DIRTY_OVERLAP
    await discard_candidate(run.review_fix.git, run.review_fix.target.repo_root)


@pytest.mark.asyncio
@pytest.mark.parametrize("passed", [True, False])
async def test_validate_group_persists_artifacts_and_terminal_group_state(tmp_path, monkeypatch, passed):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    runner = TaskRunner(_Sessions(), work_dir=tmp_path / "runner")
    await runner.create_review_fix(
        ReviewFixMetadata(
            state=ReviewFixState.AWAITING_VALIDATION,
            target=ReviewFixTargetSnapshot(dirty_fingerprint="fingerprint"),
            git=ReviewFixGitRecord(candidate_worktree_path=str(candidate)),
            groups=[ReviewFixDependencyGroup(group_id="group-1", finding_keys=["finding-1"])],
        ),
        task_id="review-fix-validation",
    )
    run = runner.get_review_fix("review-fix-validation")
    assert run.review_fix is not None
    run.review_fix.state = ReviewFixState.AWAITING_VALIDATION
    run.review_fix.revision = 0
    run.revision = 0

    async def fake_run_tests(_command, _cwd):
        return passed, "validation output"

    monkeypatch.setattr("kiro_crew.review_fix.run_tests", fake_run_tests)
    updated, result = await validate_group(
        runner,
        "review-fix-validation",
        "group-1",
        expected_revision=0,
        expected_group_revision=0,
        test_command=["pytest", "-q"],
        build_command=["npm", "run", "build"],
        artifact_dir=tmp_path / "artifacts",
    )

    assert result is passed
    assert updated.review_fix is not None
    expected_state = ReviewFixState.READY_TO_APPLY if passed else ReviewFixState.BLOCKED_VALIDATION
    assert updated.review_fix.state is expected_state
    group = updated.review_fix.groups[0]
    expected_group_state = "ready_to_apply" if passed else "proposed"
    assert group.state.value == expected_group_state
    assert group.revision == 2
    assert len(group.validation_runs) == 2
    assert all(Path(item.artifact_path).is_file() for item in group.validation_runs)
    assert len(updated.review_fix.artifact_paths) == 2
