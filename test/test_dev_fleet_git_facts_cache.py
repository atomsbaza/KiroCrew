"""Fleet-poll git facts cache (#23364).

An open /fleet tab rebuilds the payload every poll. Each rebuild used to run
~8 git commands in every worktree even when nothing had moved. These tests drive
REAL git repositories (a main checkout plus a linked worktree) and count the
spawns, so they pin the behaviour that matters: an unchanged worktree costs no
git, every kind of change is noticed, and the destructive-path readers stay
uncached.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from kiro_crew.apps.builtins.dev_fleet import (
    fleet_state,
    live,
    repository,
    runtime,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        env=_ENV,
    ).stdout.strip()


def _commit(cwd: Path, name: str, text: str = "x\n") -> None:
    (cwd / name).write_text(text)
    _git(cwd, "add", name)
    _git(cwd, "commit", "-q", "-m", f"add {name}")


@pytest.fixture
def fleet_repo(tmp_path, monkeypatch):
    """main checkout + linked worktree on ``feat``; origin/main = main's HEAD."""
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    _commit(main, "base.txt")
    _git(main, "update-ref", "refs/remotes/origin/main", "HEAD")
    wt = tmp_path / "wt-feat"
    _git(main, "worktree", "add", "-q", "-b", "feat", str(wt))
    _commit(wt, "feature.txt")

    calls: list[list[str]] = []

    async def real_run(cmd, *, cwd=None, env=None, timeout=30, mode="standard"):
        calls.append(list(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_ENV,
        )
        out, err = await proc.communicate()
        return proc.returncode, out.decode(), err.decode()

    monkeypatch.setattr(runtime, "_run_cmd", real_run)
    monkeypatch.setattr(repository, "_UPSTREAM_REMOTE", "origin")
    monkeypatch.setattr(repository, "BASE_BRANCH", "main")
    monkeypatch.setattr(repository, "_GIT_FACTS", {}, raising=False)

    def spawns_in(path: Path) -> list[list[str]]:
        return [c for c in calls if c[1:3] == ["-C", str(path)]]

    return main, wt, calls, spawns_in


@pytest.mark.asyncio
async def test_unchanged_worktree_runs_no_git_on_second_read(fleet_repo):
    main, wt, calls, spawns_in = fleet_repo

    first = await repository._git_facts(str(wt))
    assert spawns_in(wt), "the first read must hit git"
    calls.clear()

    second = await repository._git_facts(str(wt))

    assert calls == []
    assert second == first
    info, ahead = first
    assert info["branch"] == "feat"
    assert info["dirty"] is False
    assert ahead == 1


@pytest.mark.asyncio
async def test_main_checkout_is_cached_too(fleet_repo):
    main, _wt, calls, spawns_in = fleet_repo
    first = await repository._git_facts(str(main))
    calls.clear()
    assert await repository._git_facts(str(main)) == first
    assert calls == []


@pytest.mark.asyncio
async def test_new_commit_on_the_branch_is_noticed(fleet_repo):
    _main, wt, calls, _spawns = fleet_repo
    before, ahead_before = await repository._git_facts(str(wt))

    _commit(wt, "more.txt")
    after, ahead_after = await repository._git_facts(str(wt))

    assert after["head_oid"] != before["head_oid"]
    assert ahead_after == ahead_before + 1


@pytest.mark.asyncio
async def test_base_ref_moving_is_noticed_without_any_head_change(fleet_repo):
    main, wt, _calls, _spawns = fleet_repo
    before, _ = await repository._git_facts(str(wt))
    assert before["behind"] == 0

    _commit(main, "upstream.txt")
    _git(main, "update-ref", "refs/remotes/origin/main", "main")
    after, _ = await repository._git_facts(str(wt))

    assert after["behind"] == 1
    assert after["head_oid"] == before["head_oid"]


@pytest.mark.asyncio
async def test_base_ref_packed_by_gc_is_still_tracked(fleet_repo):
    main, wt, calls, _spawns = fleet_repo
    await repository._git_facts(str(wt))
    _git(main, "pack-refs", "--all")
    # Packing rewrites packed-refs and deletes the loose refs: a changed
    # signature, so one recompute is expected, then it must settle again.
    await repository._git_facts(str(wt))
    calls.clear()
    await repository._git_facts(str(wt))
    assert calls == []

    _commit(main, "upstream.txt")
    _git(main, "update-ref", "refs/remotes/origin/main", "main")
    after, _ = await repository._git_facts(str(wt))
    assert after["behind"] == 1


@pytest.mark.asyncio
async def test_branch_checkout_and_detach_are_noticed(fleet_repo):
    _main, wt, _calls, _spawns = fleet_repo
    first, _ = await repository._git_facts(str(wt))
    assert first["branch"] == "feat"

    _git(wt, "checkout", "-q", "--detach")
    detached, _ = await repository._git_facts(str(wt))

    assert detached["branch"] == "HEAD"


@pytest.mark.asyncio
async def test_staging_a_change_is_noticed_via_the_index(fleet_repo):
    _main, wt, _calls, _spawns = fleet_repo
    clean, _ = await repository._git_facts(str(wt))
    assert clean["dirty"] is False

    (wt / "feature.txt").write_text("changed\n")
    _git(wt, "add", "feature.txt")
    after, _ = await repository._git_facts(str(wt))

    assert after["dirty"] is True


@pytest.mark.asyncio
async def test_unstaged_edit_is_picked_up_when_the_dirty_ttl_lapses(fleet_repo, monkeypatch):
    """No stat of git's own files shows a worktree edit, so `dirty` has a TTL."""
    _main, wt, calls, spawns_in = fleet_repo
    await repository._git_facts(str(wt))
    (wt / "feature.txt").write_text("edited, not staged\n")

    calls.clear()
    within_ttl, _ = await repository._git_facts(str(wt))
    assert calls == []
    assert within_ttl["dirty"] is False

    monkeypatch.setattr(repository, "_GIT_FACTS_DIRTY_TTL", -1.0)
    calls.clear()
    lapsed, ahead = await repository._git_facts(str(wt))

    assert lapsed["dirty"] is True
    assert ahead == 1
    # Only the status query: the ref-derived facts were still valid.
    assert [c[3] for c in spawns_in(wt)] == ["status"]


@pytest.mark.asyncio
async def test_destructive_path_readers_never_use_the_cache(fleet_repo):
    """Discard/removal decisions ask git fresh, even when the row is cached."""
    _main, wt, calls, spawns_in = fleet_repo
    await repository._git_facts(str(wt))
    (wt / "scratch.log").write_text("untracked\n")
    (wt / "feature.txt").write_text("edited\n")

    calls.clear()
    tracked, untracked = await repository._dirty_split(str(wt))
    real = await repository._real_dirty(str(wt))

    assert tracked is True
    assert untracked == ["scratch.log"]
    assert real is True
    assert spawns_in(wt), "the uncached readers must actually spawn git"


@pytest.mark.asyncio
async def test_failed_read_is_not_pinned(fleet_repo, monkeypatch):
    _main, wt, calls, _spawns = fleet_repo
    real_ahead = repository._git_ahead
    monkeypatch.setattr(repository, "_git_ahead", AsyncMock(return_value=None))
    _info, ahead = await repository._git_facts(str(wt))
    assert ahead is None

    monkeypatch.setattr(repository, "_git_ahead", real_ahead)
    calls.clear()
    _info, ahead = await repository._git_facts(str(wt))

    assert ahead == 1
    assert calls, "a transient failure must be retried, not cached"


@pytest.mark.asyncio
async def test_non_checkout_path_falls_through_to_git(tmp_path, monkeypatch):
    info = {"head_oid": "a" * 40, "dirty": False}
    git_info = AsyncMock(return_value=info)
    git_ahead = AsyncMock(return_value=0)
    monkeypatch.setattr(repository, "_git_info", git_info)
    monkeypatch.setattr(repository, "_git_ahead", git_ahead)
    monkeypatch.setattr(repository, "_GIT_FACTS", {})

    for _ in range(2):
        assert await repository._git_facts(str(tmp_path / "missing")) == (info, 0)

    assert git_info.await_count == 2
    assert git_ahead.await_count == 2
    assert repository._GIT_FACTS == {}


@pytest.mark.asyncio
async def test_reftable_layout_is_never_cached(fleet_repo):
    _main, wt, calls, _spawns = fleet_repo
    gitdir, common = repository._git_dirs(str(wt))
    (common / "reftable").mkdir()

    await repository._git_facts(str(wt))
    calls.clear()
    await repository._git_facts(str(wt))

    assert calls, "an unrecognised ref backend must be re-queried every time"
    assert repository._GIT_FACTS == {}


@pytest.mark.asyncio
async def test_retain_drops_removed_worktrees(fleet_repo):
    main, wt, _calls, _spawns = fleet_repo
    await repository._git_facts(str(wt))
    await repository._git_facts(str(main))
    assert set(repository._GIT_FACTS) == {str(wt), str(main)}

    repository._git_facts_retain({str(main)})

    assert set(repository._GIT_FACTS) == {str(main)}


@pytest.mark.asyncio
async def test_fleet_poll_with_nothing_changed_spawns_no_git_in_any_worktree(fleet_repo):
    """The reported bug: poll #2 of an idle fleet must not touch any worktree."""
    main, wt, calls, spawns_in = fleet_repo
    worktrees = [
        {"path": str(main), "branch": "main", "is_main": True},
        {"path": str(wt), "branch": "feat", "is_main": False},
    ]
    ctx = {"issues": [], "tickets": [], "summary": None}
    with ExitStack() as stack:
        for owner, attr, repl in (
            (live, "_live_worktree_path", AsyncMock(return_value=None)),
            (repository, "_discover_worktrees", AsyncMock(return_value=worktrees)),
            (fleet_state, "_pr_status_cached", AsyncMock(return_value=None)),
            (fleet_state, "_context_cached", AsyncMock(return_value=ctx)),
            (live, "_gateway_service_active", AsyncMock(return_value=False)),
            (runtime, "_load_cfg", lambda: None),
        ):
            stack.enter_context(patch.object(owner, attr, repl))

        first = await fleet_state._build_fleet()
        assert spawns_in(wt) and spawns_in(main)
        calls.clear()

        second = await fleet_state._build_fleet()

    assert spawns_in(wt) == []
    assert spawns_in(main) == []
    rows_a = {w["name"]: w for w in first["worktrees"]}
    rows_b = {w["name"]: w for w in second["worktrees"]}
    for name in ("main", "wt-feat"):
        for field in ("branch", "head", "dirty", "behind", "shipped", "last_updated_at"):
            assert rows_a[name][field] == rows_b[name][field]
