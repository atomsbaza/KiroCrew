"""Local working-tree review primitives.

This module deliberately owns the deterministic part of the local review loop:
Git acquisition, line anchoring, bounded reviewer context, persistence-shaped
models, and reconciliation. The LLM adapter can consume these structures without
having to parse terminal output or invent its own notion of a changed line.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from sage_lib import store

from kiro_crew import platform_compat
from kiro_crew.apps.builtins.auto_improvement.spine.git_safety import git_argv
from kiro_crew.sandbox import run_limited, sandboxed_spawn_argv
from kiro_crew.security import is_sensitive_path

MAX_FILES = 100
MAX_DIFF_BYTES = 512 * 1024
MAX_CONTEXT_BYTES = 24 * 1024
MAX_FINDINGS = 100
SKIP_PARTS = {".git", "node_modules", "dist", "build", "vendor"}
SKIP_SUFFIXES = (".min.js", ".min.css", ".map")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_FILE = re.compile(r"^diff --git a/(.+) b/(.+)$")

FindingStatus = Literal["open", "accepted", "fixing", "resolved", "dismissed", "stale"]
LineKind = Literal["context", "add", "delete"]


@dataclass(frozen=True)
class ReviewDiffLine:
    kind: LineKind
    content: str
    old_line: int | None
    new_line: int | None


@dataclass(frozen=True)
class ReviewDiffHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: tuple[ReviewDiffLine, ...]


@dataclass(frozen=True)
class ReviewDiffFile:
    old_path: str | None
    new_path: str | None
    status: str
    language: str | None
    is_binary: bool
    additions: int
    deletions: int
    hunks: tuple[ReviewDiffHunk, ...]

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""

    def changed_lines(self) -> set[int]:
        return {
            line.new_line
            for hunk in self.hunks
            for line in hunk.lines
            if line.kind == "add" and line.new_line is not None
        }


@dataclass(frozen=True)
class ReviewDiff:
    repository: str
    revision: str
    base_revision: str | None
    mode: str
    files: tuple[ReviewDiffFile, ...]
    skipped_files: tuple[str, ...] = ()
    warning: str | None = None

    @property
    def reviewable_paths(self) -> set[str]:
        return {item.path for item in self.files if not item.is_binary and item.path}


@dataclass
class ReviewFinding:
    id: str
    session_id: str
    revision: str
    file: str
    side: Literal["new", "old"]
    line: int
    end_line: int | None
    severity: Literal["info", "warning", "error"]
    category: str | None
    title: str
    message: str
    suggestion: str | None
    confidence: float | None
    status: FindingStatus = "open"
    reviewer: str | None = None
    fingerprint: str = ""
    user_instruction: str | None = None
    created_at: str = field(default_factory=lambda: _now())
    updated_at: str = field(default_factory=lambda: _now())

    def to_dict(self) -> dict:
        return asdict(self)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
    return env


def _run_git_process(repo: Path, *args: str, timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    argv, env, cleanup = sandboxed_spawn_argv(git_argv(repo, *args), env=_git_env())
    try:
        return run_limited(
            argv,
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    finally:
        if cleanup:
            Path(cleanup).unlink(missing_ok=True)


def _run_git(repo: Path, *args: str, timeout: float = 20.0) -> str:
    proc = _run_git_process(repo, *args, timeout=timeout)
    if proc.returncode:
        raise ValueError(proc.stderr.strip() or "git command failed")
    return proc.stdout


def validate_repository(raw: str) -> Path:
    """Resolve a caller path and prove it is a Git worktree."""
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError("repository path must be absolute")
    resolved = path.resolve()
    if is_sensitive_path(str(resolved)):
        raise ValueError("repository path is not reviewable")
    if not resolved.is_dir():
        raise ValueError("repository path must be a directory")
    top_level = Path(_run_git(resolved, "rev-parse", "--show-toplevel").strip()).resolve()
    if top_level != resolved:
        raise ValueError("repository path is not the worktree root")
    return resolved


def _skip_path(path: str) -> bool:
    parts = Path(path).parts
    return any(part in SKIP_PARTS for part in parts) or path.endswith(SKIP_SUFFIXES)


def _language(path: str) -> str | None:
    suffix = Path(path).suffix.lower()
    return suffix[1:] if suffix else None


def _parse_diff(text: str, repository: str, revision: str, mode: str) -> tuple[ReviewDiffFile, ...]:
    files: list[ReviewDiffFile] = []
    current: dict | None = None
    hunk_lines: list[ReviewDiffLine] = []
    hunks: list[ReviewDiffHunk] = []
    additions = deletions = 0
    old_line = new_line = 0

    def finish_hunk() -> None:
        nonlocal hunk_lines
        if current is not None and current.get("hunk") is not None:
            start = current["hunk"]
            hunks.append(
                ReviewDiffHunk(
                    old_start=start[0],
                    old_count=start[1],
                    new_start=start[2],
                    new_count=start[3],
                    lines=tuple(hunk_lines),
                )
            )
        hunk_lines = []

    def finish_file() -> None:
        nonlocal current, hunks, additions, deletions
        if current is None:
            return
        finish_hunk()
        path = current["new_path"] or current["old_path"] or ""
        if not _skip_path(path):
            files.append(ReviewDiffFile(
                current["old_path"], current["new_path"], current["status"], _language(path),
                current["binary"], additions, deletions, tuple(hunks),
            ))
        hunks = []
        additions = deletions = 0
        current = None

    for raw in text.splitlines():
        match = _FILE.match(raw)
        if match:
            finish_file()
            current = {"old_path": match.group(1), "new_path": match.group(2),
                       "status": "modified", "binary": False, "hunk": None}
            continue
        if current is None:
            continue
        if raw.startswith("Binary files "):
            current["binary"] = True
            continue
        if raw.startswith("new file mode"):
            current["status"] = "added"
        elif raw.startswith("deleted file mode"):
            current["status"] = "deleted"
        elif raw.startswith("rename from"):
            current["status"] = "renamed"
        match = _HUNK.match(raw)
        if match:
            finish_hunk()
            old_line = int(match.group(1))
            old_count = int(match.group(2) or 1)
            new_line = int(match.group(3))
            new_count = int(match.group(4) or 1)
            current["hunk"] = (old_line, old_count, new_line, new_count)
            continue
        if current.get("hunk") is None or raw.startswith("\\ No newline"):
            continue
        marker = raw[:1]
        content = raw[1:] if marker in " +-" else raw
        if marker == "+":
            hunk_lines.append(ReviewDiffLine("add", content, None, new_line))
            additions += 1
            new_line += 1
        elif marker == "-":
            hunk_lines.append(ReviewDiffLine("delete", content, old_line, None))
            deletions += 1
            old_line += 1
        elif marker == " ":
            hunk_lines.append(ReviewDiffLine("context", content, old_line, new_line))
            old_line += 1
            new_line += 1
    finish_file()
    return tuple(files)


def working_tree_diff(repository: str | Path, mode: str = "all-working-tree") -> ReviewDiff:
    repo = validate_repository(str(repository))
    if mode not in {"unstaged", "staged", "all-working-tree"}:
        raise ValueError("unsupported review scope")
    head = _run_git(repo, "rev-parse", "HEAD").strip()
    args = ["diff", "--no-color", "--find-renames", "--unified=3"]
    if mode == "staged":
        args.append("--cached")
    elif mode == "all-working-tree":
        args.extend(["HEAD"])
    diff_text = _run_git(repo, *args)
    skipped: list[str] = []
    if mode == "all-working-tree":
        untracked = _run_git(repo, "ls-files", "--others", "--exclude-standard", "-z")
        for raw_path in untracked.split("\0"):
            path = raw_path
            if not path:
                continue
            if _skip_path(path):
                skipped.append(path)
                continue
            if diff_text and not diff_text.endswith("\n"):
                diff_text += "\n"
            proc = _run_git_process(
                repo, "diff", "--no-color", "--no-index", "--unified=3", "--",
                os.devnull, path,
            )
            if proc.returncode in {0, 1}:
                diff_text += proc.stdout
    digest = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()[:12]
    revision = f"{head} + working-tree:{digest}"
    files = _parse_diff(diff_text[:MAX_DIFF_BYTES], str(repo), revision, mode)
    warning = (
        "diff exceeded the review byte limit; review is partial"
        if len(diff_text.encode()) > MAX_DIFF_BYTES
        else None
    )
    return ReviewDiff(str(repo), revision, head, mode, files, tuple(skipped), warning)


def build_context(diff: ReviewDiff) -> str:
    blocks: list[str] = []
    for item in diff.files[:MAX_FILES]:
        lines: list[str] = []
        for hunk in item.hunks:
            lines.extend(f"{line.kind[0]} {line.new_line or line.old_line}: {line.content}"
                         for line in hunk.lines)
        blocks.append(f"FILE {item.path}\n" + "\n".join(lines))
    guidance: list[str] = []
    for name in ("AGENTS.md", "CONTRIBUTING.md"):
        path = Path(diff.repository) / name
        if path.is_file() and not path.is_symlink():
            try:
                guidance.append(f"GUIDANCE {name}\n{path.read_text(encoding='utf-8')[:4000]}")
            except OSError:
                continue
    context = (f"REPOSITORY: {diff.repository}\nREVISION: {diff.revision}\nSCOPE: {diff.mode}\n\n"
               + "\n\n".join(guidance + blocks))
    return context[:MAX_CONTEXT_BYTES]


def validate_finding(raw: dict, diff: ReviewDiff, session_id: str) -> ReviewFinding:
    if not isinstance(raw, dict):
        raise ValueError("finding must be an object")
    path = str(raw.get("file") or "")
    if path not in diff.reviewable_paths:
        raise ValueError("finding file is outside the reviewed diff")
    side = raw.get("side", "new")
    line = raw.get("line")
    severity = raw.get("severity")
    if side not in {"new", "old"} or not isinstance(line, int) or isinstance(line, bool):
        raise ValueError("finding anchor is invalid")
    target_file = next((item for item in diff.files if item.path == path), None)
    if target_file is None:
        raise ValueError("finding file is outside the reviewed diff")
    anchor_lines = target_file.changed_lines() if side == "new" else {
        item.old_line
        for hunk in target_file.hunks
        for item in hunk.lines
        if item.kind == "delete" and item.old_line is not None
    }
    if line not in anchor_lines:
        raise ValueError("inline finding must reference a changed line")
    end_line = raw.get("end_line")
    if end_line is not None and (
        not isinstance(end_line, int) or isinstance(end_line, bool) or end_line < line
    ):
        raise ValueError("finding end_line is invalid")
    if end_line is not None and any(
        anchor not in anchor_lines for anchor in range(line, end_line + 1)
    ):
        raise ValueError("finding range must reference changed lines")
    if severity not in {"info", "warning", "error"}:
        raise ValueError("finding severity is invalid")
    title = store.redact_text(str(raw.get("title") or "").strip())
    message = store.redact_text(str(raw.get("message") or "").strip())
    if not title or not message:
        raise ValueError("finding title and message are required")
    normalized = " ".join(message.lower().split())
    fingerprint = hashlib.sha256(
        f"{path}|{raw.get('category', '')}|{normalized}".encode()
    ).hexdigest()[:20]
    confidence = raw.get("confidence")
    if confidence is not None and (
        not isinstance(confidence, (int, float))
        or isinstance(confidence, bool)
        or not 0 <= confidence <= 1
    ):
        raise ValueError("finding confidence is invalid")
    now = _now()
    suggestion = (
        store.redact_text(str(raw.get("suggestion")))
        if raw.get("suggestion")
        else None
    )
    return ReviewFinding(
        uuid.uuid4().hex[:12], session_id, diff.revision, path, side, line, end_line,
        severity, str(raw.get("category")) if raw.get("category") else None,
        title, message, suggestion, confidence,
        reviewer=str(raw.get("reviewer")) if raw.get("reviewer") else None,
        fingerprint=fingerprint, created_at=now, updated_at=now,
    )


def reconcile_findings(
    previous: list[ReviewFinding], current: list[ReviewFinding]
) -> list[ReviewFinding]:
    current_by_fingerprint = {item.fingerprint: item for item in current}
    resolved: list[ReviewFinding] = []
    for old in previous:
        match = current_by_fingerprint.get(old.fingerprint)
        if match is None:
            old.status = "resolved" if old.status not in {"dismissed", "stale"} else old.status
            old.updated_at = _now()
            resolved.append(old)
        elif old.status in {"dismissed", "accepted", "fixing"}:
            match.status = old.status
            match.user_instruction = old.user_instruction
    return [*current, *resolved]


def session_path(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id)
    return store.data_dir() / "local-reviews" / f"{safe}.json"


def save_session(session: dict) -> None:
    path = session_path(str(session["id"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    if platform_compat.is_link_or_junction(path.parent):
        raise ValueError("refusing to write through a local-review directory link")
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.",
            suffix=".tmp", delete=False,
        ) as tmp:
            tmp_name = tmp.name
            tmp.write(json.dumps(session, indent=2))
            tmp.flush()
            os.fchmod(tmp.fileno(), 0o600)
        os.replace(tmp_name, path)
    finally:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass


def load_session(session_id: str) -> dict | None:
    path = session_path(session_id)
    data = store.read_json_nolink(path, path.parent)
    return data if isinstance(data, dict) else None
