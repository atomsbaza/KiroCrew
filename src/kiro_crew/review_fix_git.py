"""Safe Git primitives for the user-approved review-fix workflow.

Generic Task Runner Git behavior remains in :mod:`git_coord`. This module only
operates on an explicitly captured target and a retained candidate worktree.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from kiro_crew import git_coord
from kiro_crew.security import is_sensitive_path, redact_credentials, redact_exfiltration_urls
from kiro_crew.task_models import ReviewFixGitRecord, ReviewFixTargetMode, ReviewFixTargetSnapshot

logger = logging.getLogger(__name__)

_CONFLICT_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")


class ReviewFixGitError(RuntimeError):
    """A bounded, user-safe error from a review-fix Git operation."""


@dataclass(frozen=True)
class ReviewFixPatch:
    """A candidate patch and the task-owned paths it may change."""

    patch_id: str
    patch_text: str
    paths: tuple[str, ...]
    patch_path: str = ""


async def _git_text(work_dir: str | Path, *args: str) -> str:
    """Run Git through the existing sandboxed Task Runner chokepoint."""
    try:
        return await git_coord._git(str(work_dir), *args)
    except Exception as exc:
        message = str(exc)
        message = redact_credentials(message)[0]
        message = redact_exfiltration_urls(message)[0]
        raise ReviewFixGitError(message) from exc


async def _try_git_text(work_dir: str | Path, *args: str) -> str:
    try:
        return await _git_text(work_dir, *args)
    except ReviewFixGitError:
        return ""


def _clean_path_list(raw: Iterable[str]) -> tuple[str, ...]:
    paths: set[str] = set()
    for value in raw:
        path = str(value).replace("\\", "/")
        if not path or path.startswith("/") or path == "." or path.startswith("../"):
            raise ReviewFixGitError("invalid task-owned path")
        if "/../" in f"/{path}/" or path.startswith("-"):
            raise ReviewFixGitError("invalid task-owned path")
        paths.add(path)
    return tuple(sorted(paths))


def _status_paths(status: str) -> tuple[list[str], list[str]]:
    tracked: set[str] = set()
    untracked: set[str] = set()
    for line in status.splitlines():
        if len(line) < 4:
            continue
        code = line[:2]
        path_text = line[3:]
        candidates = path_text.split(" -> ") if " -> " in path_text else [path_text]
        if code == "??":
            untracked.update(candidates)
        else:
            tracked.update(candidates)
    return sorted(tracked), sorted(untracked)


async def inspect_target(
    target_path: str | Path,
    *,
    mode: ReviewFixTargetMode = ReviewFixTargetMode.CURRENT_BRANCH,
) -> ReviewFixTargetSnapshot:
    """Capture repository identity, HEAD, dirty paths, and a stable fingerprint."""
    path = Path(target_path).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise ReviewFixGitError("review-fix target is not a directory")
    if is_sensitive_path(str(path)):
        raise ReviewFixGitError("review-fix target is a sensitive path")
    repo_root = Path((await _git_text(path, "rev-parse", "--show-toplevel")).strip()).resolve()
    if is_sensitive_path(str(repo_root)):
        raise ReviewFixGitError("review-fix repository is a sensitive path")
    try:
        path.relative_to(repo_root)
    except ValueError as exc:
        raise ReviewFixGitError("review-fix target is outside its repository") from exc

    branch = (await _git_text(path, "rev-parse", "--abbrev-ref", "HEAD")).strip()
    head_sha = (await _git_text(path, "rev-parse", "HEAD")).strip()
    status = await _git_text(path, "status", "--porcelain=v1", "--untracked-files=all")
    tracked, untracked = _status_paths(status)
    fingerprint = hashlib.sha256(status.encode("utf-8")).hexdigest()
    upstream = (
        await _try_git_text(path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    ).strip()
    remote = upstream.split("/", 1)[0] if "/" in upstream else ""
    if not remote:
        remotes = (await _try_git_text(path, "remote")).splitlines()
        remote = "origin" if "origin" in remotes else (remotes[0].strip() if remotes else "")
    target_ref = branch if branch and branch != "HEAD" else head_sha
    return ReviewFixTargetSnapshot(
        mode=mode,
        repo_root=str(repo_root),
        target_path=str(path),
        target_ref=target_ref,
        branch_name=branch,
        head_sha=head_sha,
        dirty_fingerprint=fingerprint,
        tracked_paths=tracked,
        untracked_paths=untracked,
        upstream=upstream,
        remote=remote,
    )


def dirty_paths(snapshot: ReviewFixTargetSnapshot) -> set[str]:
    """Return the conservative path set that must not overlap silently."""
    return set(snapshot.tracked_paths) | set(snapshot.untracked_paths)


def dirty_overlap(snapshot: ReviewFixTargetSnapshot, paths: Iterable[str]) -> list[str]:
    """Return shared paths; separate hunks never bypass this safety check."""
    return sorted(dirty_paths(snapshot) & set(_clean_path_list(paths)))


def assert_target_unchanged(
    expected: ReviewFixTargetSnapshot,
    current: ReviewFixTargetSnapshot,
) -> None:
    """Raise when Apply is no longer operating on the captured target."""
    if (
        expected.repo_root != current.repo_root
        or expected.target_path != current.target_path
        or expected.branch_name != current.branch_name
        or expected.head_sha != current.head_sha
        or expected.dirty_fingerprint != current.dirty_fingerprint
    ):
        raise ReviewFixGitError("review-fix target changed since confirmation")


async def create_candidate(
    target: ReviewFixTargetSnapshot,
    candidate_path: str | Path,
    candidate_branch: str,
) -> ReviewFixGitRecord:
    """Create a candidate worktree from the captured target HEAD."""
    if not target.repo_root or not target.head_sha:
        raise ReviewFixGitError("review-fix target snapshot is incomplete")
    path = Path(candidate_path).expanduser().resolve()
    if path.exists():
        raise ReviewFixGitError("review-fix candidate path already exists")
    if is_sensitive_path(str(path)):
        raise ReviewFixGitError("review-fix candidate is a sensitive path")
    path.parent.mkdir(parents=True, exist_ok=True)
    await _git_text(target.repo_root, "check-ref-format", "--branch", candidate_branch)
    await _git_text(
        target.repo_root,
        "worktree",
        "add",
        "-b",
        candidate_branch,
        str(path),
        target.head_sha,
    )
    return ReviewFixGitRecord(
        candidate_worktree_path=str(path),
        candidate_branch=candidate_branch,
        candidate_ref=candidate_branch,
        remote=target.remote,
        upstream=target.upstream,
    )


async def discard_candidate(record: ReviewFixGitRecord, repo_root: str) -> None:
    """Remove only the retained candidate worktree."""
    if not record.candidate_worktree_path:
        return
    await _git_text(repo_root, "worktree", "remove", record.candidate_worktree_path, "--force")


async def candidate_patch(
    candidate_path: str | Path,
    base_sha: str,
    paths: Iterable[str] = (),
) -> ReviewFixPatch:
    """Export an exact binary-capable patch and its owned path set."""
    clean_paths = _clean_path_list(paths)
    diff_args = ["diff", "--binary", "--no-ext-diff", base_sha]
    if clean_paths:
        diff_args.extend(["--", *clean_paths])
    patch_text = await _git_text(candidate_path, *diff_args)
    name_args = ["diff", "--name-only", "--no-ext-diff", base_sha]
    if clean_paths:
        name_args.extend(["--", *clean_paths])
    discovered = _clean_path_list((await _git_text(candidate_path, *name_args)).splitlines())
    patch_id = hashlib.sha256(patch_text.encode("utf-8")).hexdigest()
    return ReviewFixPatch(patch_id=patch_id, patch_text=patch_text, paths=discovered)


async def write_patch(patch: ReviewFixPatch, patch_path: str | Path) -> ReviewFixPatch:
    """Persist a candidate patch outside the target worktree."""
    path = Path(patch_path).expanduser().resolve()
    if is_sensitive_path(str(path)):
        raise ReviewFixGitError("review-fix patch path is a sensitive path")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(patch.patch_text, encoding="utf-8")
    return ReviewFixPatch(patch.patch_id, patch.patch_text, patch.paths, str(path))


def _safe_patch_path(repo_root: Path, patch_path: str | Path) -> Path:
    path = Path(patch_path).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise ReviewFixGitError("review-fix patch is missing")
    if is_sensitive_path(str(path)):
        raise ReviewFixGitError("review-fix patch is a sensitive path")
    return path


async def apply_patch(
    target: ReviewFixTargetSnapshot,
    patch: ReviewFixPatch,
) -> list[str]:
    """Apply only the supplied patch; never reset or stash the target."""
    if not patch.patch_path:
        raise ReviewFixGitError("review-fix patch has not been written")
    repo_root = Path(target.repo_root).resolve()
    patch_path = _safe_patch_path(repo_root, patch.patch_path)
    await _git_text(repo_root, "apply", "--check", "--3way", str(patch_path))
    await _git_text(repo_root, "apply", "--3way", "--whitespace=nowarn", str(patch_path))
    conflicts = (await _try_git_text(repo_root, "diff", "--name-only", "--diff-filter=U")).splitlines()
    if conflicts:
        raise ReviewFixGitError("review-fix apply produced unresolved conflicts")
    for relative in patch.paths:
        candidate = (repo_root / relative).resolve()
        try:
            candidate.relative_to(repo_root)
        except ValueError as exc:
            raise ReviewFixGitError("review-fix patch escaped repository root") from exc
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8", errors="replace")
            if any(marker in text for marker in _CONFLICT_MARKERS):
                raise ReviewFixGitError("review-fix apply left conflict markers")
    return list(patch.paths)


async def stage_paths(repo_root: str | Path, paths: Iterable[str]) -> list[str]:
    """Stage exactly the task-owned paths and return the staged scope."""
    clean_paths = _clean_path_list(paths)
    if not clean_paths:
        raise ReviewFixGitError("review-fix commit has no owned paths")
    await _git_text(repo_root, "add", "--", *clean_paths)
    staged = _clean_path_list(
        (await _git_text(repo_root, "diff", "--cached", "--name-only")).splitlines()
    )
    if not set(staged).issubset(set(clean_paths)):
        raise ReviewFixGitError("review-fix staging escaped task-owned paths")
    return list(staged)


async def commit_group(repo_root: str | Path, paths: Iterable[str], message: str) -> str:
    """Commit only task-owned paths after explicit user confirmation."""
    if not message.strip():
        raise ReviewFixGitError("review-fix commit message is empty")
    staged = await stage_paths(repo_root, paths)
    if not staged:
        raise ReviewFixGitError("review-fix commit has no changes")
    await _git_text(repo_root, "commit", "-m", message.strip(), "--only", "--", *staged)
    return (await _git_text(repo_root, "rev-parse", "HEAD")).strip()


async def push_preview(repo_root: str | Path, remote: str, branch: str) -> dict[str, object]:
    """Describe a push without contacting the remote."""
    if not remote or not branch or branch == "HEAD":
        raise ReviewFixGitError("review-fix push target is incomplete")
    upstream = (
        await _try_git_text(repo_root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    ).strip()
    commits = (
        await _try_git_text(repo_root, "log", "--oneline", f"{upstream}..HEAD") if upstream else ""
    ).splitlines()
    files = (
        await _try_git_text(repo_root, "diff", "--name-only", f"{upstream}..HEAD") if upstream else ""
    ).splitlines()
    return {
        "remote": remote,
        "branch": branch,
        "upstream": upstream,
        "commits": commits,
        "files": files,
        "diverged": bool(upstream and not commits and files),
    }


async def push(repo_root: str | Path, remote: str, branch: str) -> dict[str, object]:
    """Push once to the named remote/branch; force-push is unavailable."""
    if not remote or not branch or branch == "HEAD":
        raise ReviewFixGitError("review-fix push target is incomplete")
    await _git_text(repo_root, "push", remote, branch)
    return {"remote": remote, "branch": branch, "pushed": True}
