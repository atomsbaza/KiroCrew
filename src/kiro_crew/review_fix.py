"""Review-fix orchestration shared by Sage and Task Runner dashboard routes."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence

from kiro_crew import review_fix_git
from kiro_crew.acp.client import model_is_unusable
from kiro_crew.security import redact_credentials, redact_exfiltration_urls
from kiro_crew.task_executor import run_tests
from kiro_crew.task_models import (
    ReviewFixDependencyGroup,
    ReviewFixFindingSnapshot,
    ReviewFixMetadata,
    ReviewFixModelResolution,
    ReviewFixState,
    ReviewFixTargetMode,
    ReviewFixValidationRun,
    Task,
)
from kiro_crew.validation import MODEL_ID_RE

if TYPE_CHECKING:
    from kiro_crew.taskrunner import TaskRunner


_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_ARTIFACT_BYTES = 256 * 1024


class ReviewFixModelResolutionError(ValueError):
    """Raised when a review-fix task cannot obtain a concrete served model."""

    code = "blocked_model_resolution"


class ReviewFixPlanError(ValueError):
    """Raised when immutable finding snapshots cannot form a safe plan."""

    def __init__(self, message: str, *, code: str = "invalid_review_fix") -> None:
        super().__init__(message)
        self.code = code


def resolve_pinned_model(
    requested_model: str,
    advertised_model_ids: Sequence[str],
    *,
    provider: str = "acp",
    resolved_at: float | None = None,
) -> ReviewFixModelResolution:
    """Resolve only a concrete, advertised model for a review-fix task.

    ``auto`` and an unknown advertised set are intentionally rejected: a fix
    task must keep using the same concrete model across retries and resumes.
    """
    requested = str(requested_model or "").strip()
    advertised = [str(value).strip() for value in advertised_model_ids if str(value).strip()]
    if not requested or requested.lower() == "auto":
        raise ReviewFixModelResolutionError("a concrete review-fix model is required")
    if not MODEL_ID_RE.fullmatch(requested):
        raise ReviewFixModelResolutionError("review-fix model id is invalid")
    if not advertised or model_is_unusable(requested, advertised):
        raise ReviewFixModelResolutionError("review-fix model is not advertised for this provider")
    return ReviewFixModelResolution(
        requested_model=requested,
        provider=str(provider or "acp"),
        resolved_model_id=requested,
        advertised_model_ids=advertised,
        resolved_at=resolved_at or time.time(),
    )


def _finding_snapshot(raw: Any, index: int) -> ReviewFixFindingSnapshot:
    if not isinstance(raw, dict):
        raise ReviewFixPlanError("finding must be an object")
    key = str(raw.get("key") or raw.get("id") or raw.get("fingerprint") or f"finding-{index}").strip()
    if not key:
        raise ReviewFixPlanError("finding key is required")
    line = raw.get("line", raw.get("start_line"))
    end_line = raw.get("end_line")
    return ReviewFixFindingSnapshot(
        key=key,
        title=str(raw.get("title") or raw.get("headline") or "").strip(),
        severity=str(raw.get("severity") or raw.get("priority") or "").strip(),
        body=str(raw.get("body") or raw.get("description") or raw.get("message") or "").strip(),
        file_path=str(raw.get("file_path") or raw.get("path") or raw.get("file") or "").strip(),
        line=int(line) if isinstance(line, (int, float)) else None,
        end_line=int(end_line) if isinstance(end_line, (int, float)) else None,
        fingerprint=str(raw.get("fingerprint") or "").strip(),
        suggested_fix=str(raw.get("suggested_fix") or raw.get("fix") or "").strip(),
    )


def build_review_fix_groups(
    findings: Sequence[ReviewFixFindingSnapshot],
    raw_groups: Sequence[Any] | None = None,
) -> list[ReviewFixDependencyGroup]:
    """Normalize a user/planner grouping while preserving hard edges."""
    by_key = {finding.key: finding for finding in findings}
    if len(by_key) != len(findings):
        raise ReviewFixPlanError("finding keys must be unique")
    if not raw_groups:
        return [
            ReviewFixDependencyGroup(
                group_id=f"group-{index}",
                finding_keys=[finding.key],
                affected_files=[finding.file_path] if finding.file_path else [],
            )
            for index, finding in enumerate(findings, start=1)
        ]

    groups: list[ReviewFixDependencyGroup] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_groups, start=1):
        if not isinstance(raw, dict):
            raise ReviewFixPlanError("dependency group must be an object")
        keys = [str(value) for value in raw.get("finding_keys", raw.get("findings", []))]
        if not keys or any(key not in by_key for key in keys) or seen.intersection(keys):
            raise ReviewFixPlanError("dependency groups must cover each finding once")
        seen.update(keys)
        files = [by_key[key].file_path for key in keys if by_key[key].file_path]
        groups.append(
            ReviewFixDependencyGroup(
                group_id=str(raw.get("group_id") or f"group-{index}"),
                finding_keys=keys,
                hard=bool(raw.get("hard", False)),
                hard_edges=[dict(edge) for edge in raw.get("hard_edges", []) if isinstance(edge, dict)],
                soft_edges=[dict(edge) for edge in raw.get("soft_edges", []) if isinstance(edge, dict)],
                reasons=[str(value) for value in raw.get("reasons", [])],
                affected_files=sorted(set(files) | {str(value) for value in raw.get("affected_files", [])}),
            )
        )
    if seen != set(by_key):
        raise ReviewFixPlanError("dependency groups must cover every selected finding")
    return groups


def build_review_fix_tasks(
    findings: Sequence[ReviewFixFindingSnapshot],
    groups: Sequence[ReviewFixDependencyGroup],
) -> list[Task]:
    """Create one Task Runner task per immutable finding snapshot."""
    tasks: list[Task] = []
    for index, finding in enumerate(findings, start=1):
        title = finding.title or finding.key
        description = finding.body or finding.suggested_fix or title
        dependencies: list[int] = []
        tasks.append(
            Task(
                index=index,
                title=title[:500],
                description=description[:5000],
                depends_on=dependencies,
                task_type="fix",
            )
        )
    return tasks


async def create_review_fix_task(
    runner: "TaskRunner",
    *,
    target_path: str | Path,
    findings: Sequence[Any],
    review_run_id: str = "",
    pr_url: str = "",
    source_head_sha: str = "",
    target_mode: ReviewFixTargetMode | str = ReviewFixTargetMode.CURRENT_BRANCH,
    requested_model: str = "",
    advertised_model_ids: Sequence[str] = (),
    provider: str = "acp",
    raw_groups: Sequence[Any] | None = None,
    task_id: str = "",
    name: str = "",
    candidate_root: str | Path | None = None,
) -> Any:
    """Create a retained candidate and a durable review-fix Task Runner run."""
    try:
        mode = target_mode if isinstance(target_mode, ReviewFixTargetMode) else ReviewFixTargetMode(str(target_mode))
    except ValueError as exc:
        raise ReviewFixPlanError("invalid review-fix target mode") from exc
    snapshots = [_finding_snapshot(raw, index) for index, raw in enumerate(findings, start=1)]
    if not snapshots:
        raise ReviewFixPlanError("at least one finding is required")
    groups = build_review_fix_groups(snapshots, raw_groups)
    tasks = build_review_fix_tasks(snapshots, groups)
    target = await review_fix_git.inspect_target(target_path, mode=mode)
    metadata = ReviewFixMetadata(
        review_run_id=review_run_id,
        pr_url=pr_url,
        source_head_sha=source_head_sha or target.head_sha,
        selected_finding_keys=[finding.key for finding in snapshots],
        finding_snapshots=snapshots,
        target=target,
        model=ReviewFixModelResolution(
            requested_model=str(requested_model or ""),
            provider=str(provider or "acp"),
            advertised_model_ids=[str(value) for value in advertised_model_ids],
        ),
        groups=groups,
    )
    run = await runner.create_review_fix(
        metadata,
        task_id=task_id,
        name=name or review_run_id or "review-fix",
        spec_content="\n".join(task.description for task in tasks),
        tasks=tasks,
    )
    task_id = run.task_id
    safe_id = _SAFE_ID_RE.sub("-", task_id).strip("-._") or "task"
    default_root = Path(target.repo_root).parent / ".kirocrew-work" / "review-fix" / safe_id
    root = Path(candidate_root).expanduser().resolve() if candidate_root else default_root
    candidate_path = root / "candidate"
    candidate_branch = f"kirocrew/review-fix/{safe_id}"
    try:
        git_record = await review_fix_git.create_candidate(target, candidate_path, candidate_branch)
    except Exception:
        await runner.delete_run(task_id)
        raise
    run = await runner.mutate_review_fix(
        task_id,
        expected_revision=0,
        action="candidate_created",
        mutate=lambda current: setattr(current, "git", git_record),
    )
    run.work_dir = git_record.candidate_worktree_path
    run.branch_name = git_record.candidate_branch
    run.repo_root = target.repo_root
    run.git_enabled = True
    await runner._apersist_runs()

    try:
        resolution = resolve_pinned_model(requested_model, advertised_model_ids, provider=provider)
    except ReviewFixModelResolutionError as exc:
        blocked_reason = str(exc)
        await runner.mutate_review_fix(
            task_id,
            expected_revision=run.revision,
            action="model_resolution_failed",
            to_state=ReviewFixState.BLOCKED_MODEL_RESOLUTION,
            mutate=lambda current: setattr(current, "blocked_reason", blocked_reason),
        )
        return runner.get_review_fix(task_id)

    run = await runner.mutate_review_fix(
        task_id,
        expected_revision=run.revision,
        action="model_resolved",
        mutate=lambda current: setattr(current, "model", resolution),
    )
    overlap = review_fix_git.dirty_overlap(target, [path for group in groups for path in group.affected_files])
    if overlap:
        run = await runner.mutate_review_fix(
            task_id,
            expected_revision=run.revision,
            action="dirty_overlap_detected",
            to_state=ReviewFixState.BLOCKED_DIRTY_OVERLAP,
            mutate=lambda current: setattr(current, "blocked_reason", ", ".join(overlap)[:2000]),
        )
        return run
    run = await runner.mutate_review_fix(
        task_id,
        expected_revision=run.revision,
        action="grouping_proposed",
        to_state=ReviewFixState.PLANNING,
        mutate=lambda current: setattr(current, "blocked_reason", ""),
    )
    return await runner.mutate_review_fix(
        task_id,
        expected_revision=run.revision,
        action="awaiting_group_confirmation",
        to_state=ReviewFixState.AWAITING_GROUP_CONFIRMATION,
        mutate=lambda current: None,
    )


async def capture_group_patch(
    runner: "TaskRunner",
    task_id: str,
    group_id: str,
    *,
    expected_revision: int,
    expected_group_revision: int,
) -> Any:
    """Capture a group patch and bump both group and task revisions."""
    run = runner.get_review_fix(task_id)
    metadata = run.review_fix
    assert metadata is not None
    group = runner.review_fix_group(run, group_id)
    patch = await review_fix_git.candidate_patch(
        metadata.git.candidate_worktree_path,
        metadata.target.head_sha,
        group.affected_files,
    )
    return await runner.mutate_review_fix(
        task_id,
        expected_revision=expected_revision,
        expected_group_revision=expected_group_revision,
        group_id=group_id,
        action="group_patch_captured",
        mutate=lambda current: _update_group_patch(current, group_id, patch),
    )


def _update_group_patch(metadata: ReviewFixMetadata, group_id: str, patch: review_fix_git.ReviewFixPatch) -> None:
    group = next(group for group in metadata.groups if group.group_id == group_id)
    group.candidate_patch_id = patch.patch_id
    group.candidate_base_sha = metadata.target.head_sha
    group.candidate_head_sha = metadata.target.head_sha
    group.revision += 1
    metadata.diff_paths = sorted(set(metadata.diff_paths) | set(patch.paths))


async def validate_group(
    runner: "TaskRunner",
    task_id: str,
    group_id: str,
    *,
    expected_revision: int,
    expected_group_revision: int,
    test_command: Sequence[str],
    build_command: Sequence[str],
    artifact_dir: str | Path | None = None,
) -> tuple[Any, bool]:
    """Run the required full test and build commands and persist bounded artifacts."""
    run = runner.get_review_fix(task_id)
    metadata = run.review_fix
    assert metadata is not None
    runner.review_fix_group(run, group_id)
    validation_state = metadata.state
    if validation_state not in {
        ReviewFixState.AWAITING_VALIDATION,
        ReviewFixState.BLOCKED_VALIDATION,
    }:
        raise ReviewFixPlanError("task is not ready for validation")
    if not test_command or not build_command:
        raise ReviewFixPlanError("full test and build commands are required")
    await runner.mutate_review_fix(
        task_id,
        expected_revision=expected_revision,
        expected_group_revision=expected_group_revision,
        group_id=group_id,
        action="validation_started",
        expected_state=validation_state,
        to_state=(
            ReviewFixState.AWAITING_VALIDATION
            if validation_state is ReviewFixState.BLOCKED_VALIDATION
            else None
        ),
        mutate=lambda current: _set_group_state(current, group_id, "validating"),
    )
    current = runner.get_review_fix(task_id)
    current_group = runner.review_fix_group(current, group_id)
    root = Path(artifact_dir or metadata.git.candidate_worktree_path) / ".kirocrew-review-fix-artifacts"
    root.mkdir(parents=True, exist_ok=True)
    validations: list[ReviewFixValidationRun] = []
    for kind, command in (("test", test_command), ("build", build_command)):
        started = time.time()
        passed, output = await run_tests(list(command), Path(metadata.git.candidate_worktree_path))
        finished = time.time()
        safe_output = redact_credentials(redact_exfiltration_urls(output or "")[0])[0]
        safe_output = safe_output[:_MAX_ARTIFACT_BYTES]
        artifact_path = root / f"{group_id}-{kind}-{int(started)}.log"
        artifact_path.write_text(safe_output, encoding="utf-8")
        validations.append(
            ReviewFixValidationRun(
                validation_id=f"{group_id}-{kind}-{int(started * 1000)}",
                group_id=group_id,
                group_revision=current_group.revision,
                kind=kind,
                command=[str(value) for value in command],
                exit_code=0 if passed else 1,
                passed=passed,
                artifact_path=str(artifact_path),
                started_at=started,
                finished_at=finished,
                duration_secs=max(0.0, finished - started),
            )
        )
    passed = all(item.passed for item in validations)
    current = runner.get_review_fix(task_id)
    return await runner.mutate_review_fix(
        task_id,
        expected_revision=current.revision,
        expected_group_revision=current_group.revision,
        group_id=group_id,
        action="validation_finished",
        to_state=ReviewFixState.READY_TO_APPLY if passed else ReviewFixState.BLOCKED_VALIDATION,
        expected_state=ReviewFixState.AWAITING_VALIDATION,
        mutate=lambda current_metadata: _finish_group_validation(
            current_metadata, group_id, validations, passed
        ),
    ), passed


def _set_group_state(metadata: ReviewFixMetadata, group_id: str, state: str) -> None:
    group = next(group for group in metadata.groups if group.group_id == group_id)
    group.state = type(group.state)(state)
    group.revision += 1


def _finish_group_validation(
    metadata: ReviewFixMetadata,
    group_id: str,
    validations: list[ReviewFixValidationRun],
    passed: bool,
) -> None:
    group = next(group for group in metadata.groups if group.group_id == group_id)
    group.validation_runs.extend(validations)
    group.state = type(group.state).READY_TO_APPLY if passed else type(group.state).PROPOSED
    group.revision += 1
    metadata.artifact_paths.extend(item.artifact_path for item in validations if item.artifact_path)
    metadata.artifact_paths = metadata.artifact_paths[-100:]
