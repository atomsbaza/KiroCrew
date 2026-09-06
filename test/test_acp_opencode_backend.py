"""The opencode backend's twins: the six guarantees codex's onboarding carried.

opencode is KNOWN but NOT selectable: it speaks ACP itself (``opencode acp``),
but a live probe of its ``session/new`` shows no enforceable permission boundary
(one ``model`` select ``configOption``, no permission mode), so there is nothing
for ``acp_tool_gate.enforce_runtime_routing`` to arm. It is therefore
deliberately excluded from ``BASELINE_SELECTABLE_BACKENDS`` — a config naming it
degrades to the default backend with the standard not-selectable warning
(``TestNotSelectableFallback``) — while staying install-probed and outside every
capability set. Each test group here mirrors the codex twin in the file the
codex one lives in, so onboarding parity is checkable side by side:

1. registry snapshot      — ``test_harness_parity.py`` / ``test_acp_capability_sets_leaf.py``
2. spawn resolution       — ``test_harness_parity.py::test_codex_spawn_keeps_its_own_branch``
3. probe state            — ``test_agent_sdk_backend_install.py`` (``TestCodexDriverSeams``)
4. capability gating      — ``test_acp_client_more_coverage.py`` (the non-member set_model test)
5. serialization          — ``test_harness_parity.py::test_codex_mcp_seam_defaults_to_empty``
6. member-dispatch exclusion — ``test_harness_parity.py`` / ``test_provider_mirrors.py``
"""

from __future__ import annotations

import inspect
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from kiro_crew import acp_backends, platform_compat
from kiro_crew.acp import client as acp_client
from kiro_crew.acp.types import (
    ACP_BACKEND_OPENCODE,
    PROVIDER_LABEL_CODEX,
    PROVIDER_LABEL_DEFAULT,
    PROVIDER_LABEL_OPENCODE,
)
from kiro_crew.acp_backends import (
    ACP_BACKENDS_ACP_RUNTIME,
    ACP_BACKENDS_ADVERTISED_MODEL_SELECTION,
    ACP_BACKENDS_COMPACT,
    ACP_BACKENDS_EFFORT_VIA_CONFIG_OPTION,
    ACP_BACKENDS_INTERNAL_SANDBOX,
    ACP_BACKENDS_KIRO_IDENTITY_STORE,
    ACP_BACKENDS_KIRO_SLASH_COMMANDS,
    ACP_BACKENDS_KNOWN,
    ACP_BACKENDS_MCP_CONFIG_HOT_RELOAD,
    ACP_BACKENDS_MEMBER_DISPATCH,
    ACP_BACKENDS_MODEL_VIA_CONFIG_OPTION,
    ACP_BACKENDS_SEED_LOCAL_SETTINGS,
    ACP_BACKENDS_SESSION_MCP_ARRAY,
    ACP_BACKENDS_SESSION_SHARING,
    ACP_BACKENDS_STEER,
    ACP_BACKEND_KIRO,
    BASELINE_SELECTABLE_BACKENDS,
    model_registry_namespace,
    resolve_selected_backend,
    selectable_backends,
)
from kiro_crew.agent_sdk import backend_install as probe


def _client(tmp_path) -> acp_client.AcpClient:
    """An AcpClient pinned to *tmp_path*, on the opencode backend."""
    return acp_client.AcpClient(work_dir=tmp_path, acp_backend=ACP_BACKEND_OPENCODE)


# ── 0. The gate: a config naming opencode never spawns one ──


class TestNotSelectableFallback:
    """The regression lock for the known-but-not-selectable decision.

    ``resolve_selected_backend`` is THE one gate between config.json and a
    spawned harness (harness-parity H4): it reads the live selectable registry
    per call, so excluding opencode from ``BASELINE_SELECTABLE_BACKENDS`` makes
    a persisted ``"opencode"`` degrade to the default backend with the warning
    instead of reaching ``AcpClient(acp_backend="opencode")`` and its spawn arm.
    """

    def test_a_config_naming_opencode_degrades_to_the_default_with_the_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="kiro_crew.acp_backends"):
            resolved = resolve_selected_backend(ACP_BACKEND_OPENCODE)
        assert resolved == ACP_BACKEND_KIRO
        assert resolved != ACP_BACKEND_OPENCODE
        opencode_lines = [r for r in caplog.records if "opencode" in r.getMessage()]
        assert opencode_lines, "the degrade was silent — an operator gets no reason"
        assert "not selectable" in opencode_lines[0].getMessage()

    def test_no_operator_surface_offers_it(self):
        """The dashboard option list and the PATCH allowlist both derive from
        ``selectable_backends()``, so the exclusion there removes opencode from
        every surface at once — there is no second list to drift."""
        assert ACP_BACKEND_OPENCODE not in selectable_backends()


# ── 1. Registry snapshot ──


class TestRegistrySnapshot:
    """Known and probed, but NOT selectable — chat-only in every capability set."""

    def test_opencode_is_known_but_not_selectable(self):
        assert ACP_BACKEND_OPENCODE in ACP_BACKENDS_KNOWN
        assert ACP_BACKEND_OPENCODE not in BASELINE_SELECTABLE_BACKENDS
        assert ACP_BACKEND_OPENCODE not in selectable_backends()

    def test_opencode_has_a_probe_behind_its_switch(self):
        """The codex invariant, adapted: even a backend no session can be served
        on gets an install row that can say what is missing — the dashboard
        renders the probe verdict whether or not the switch is offered."""
        assert ACP_BACKEND_OPENCODE in probe._PROBES

    def test_policy_can_name_it(self):
        assert acp_backends.POLICY_ID_BY_BACKEND[ACP_BACKEND_OPENCODE] == "opencode"

    def test_registry_namespace_is_mapped(self):
        assert model_registry_namespace(ACP_BACKEND_OPENCODE) == "acp"

    def test_joins_no_capability_set(self):
        """v1 is chat-only. Joining a set is an opt-in with evidence (H6); this
        pin forces every future join to be seen here first."""
        for name, members in (
            ("ACP_BACKENDS_SESSION_MCP_ARRAY", ACP_BACKENDS_SESSION_MCP_ARRAY),
            ("ACP_BACKENDS_SESSION_SHARING", ACP_BACKENDS_SESSION_SHARING),
            ("ACP_BACKENDS_MEMBER_DISPATCH", ACP_BACKENDS_MEMBER_DISPATCH),
            ("ACP_BACKENDS_STEER", ACP_BACKENDS_STEER),
            ("ACP_BACKENDS_COMPACT", ACP_BACKENDS_COMPACT),
            ("ACP_BACKENDS_INTERNAL_SANDBOX", ACP_BACKENDS_INTERNAL_SANDBOX),
            ("ACP_BACKENDS_ACP_RUNTIME", ACP_BACKENDS_ACP_RUNTIME),
            ("ACP_BACKENDS_KIRO_IDENTITY_STORE", ACP_BACKENDS_KIRO_IDENTITY_STORE),
            ("ACP_BACKENDS_MODEL_VIA_CONFIG_OPTION", ACP_BACKENDS_MODEL_VIA_CONFIG_OPTION),
            ("ACP_BACKENDS_EFFORT_VIA_CONFIG_OPTION", ACP_BACKENDS_EFFORT_VIA_CONFIG_OPTION),
            (
                "ACP_BACKENDS_ADVERTISED_MODEL_SELECTION",
                ACP_BACKENDS_ADVERTISED_MODEL_SELECTION,
            ),
            ("ACP_BACKENDS_SEED_LOCAL_SETTINGS", ACP_BACKENDS_SEED_LOCAL_SETTINGS),
            ("ACP_BACKENDS_KIRO_SLASH_COMMANDS", ACP_BACKENDS_KIRO_SLASH_COMMANDS),
            ("ACP_BACKENDS_MCP_CONFIG_HOT_RELOAD", ACP_BACKENDS_MCP_CONFIG_HOT_RELOAD),
        ):
            assert ACP_BACKEND_OPENCODE not in members, (
                f"opencode joined {name}: that is an opt-in that needs wire "
                f"evidence and its own test edits, not a silent inheritance"
            )


# ── 2. Spawn resolution ──


class TestSpawnResolution:
    """The resolver ladder: override env → mise → augmented PATH, argv = [opencode, acp]."""

    @staticmethod
    def _fake_binary(tmp_path: Path, name: str = "opencode") -> str:
        """A REAL executable file: the resolver refuses a candidate that is not
        one (that refusal is loader behavior, not something to stub out). On
        Windows there IS no execute bit — ``platform_compat.is_executable_file``
        decides runnability by the file's extension — so the fixture takes a
        ``.exe`` name there, the shape real Windows toolchains ship (the same
        pattern ``test_node_toolchain_resolution.py`` uses for its node/npm
        fakes)."""
        if platform_compat.IS_WINDOWS:
            name = f"{name}.exe"
        binary = tmp_path / name
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        return str(binary)

    def _resolve(self, monkeypatch, tmp_path, *, override=None, mise=None, which=None):
        monkeypatch.delenv("OPENCODE_ACP_BIN", raising=False)
        if override is not None:
            monkeypatch.setenv("OPENCODE_ACP_BIN", str(override))
        monkeypatch.setattr(acp_client, "_mise_which", lambda tool: mise)
        monkeypatch.setattr(
            acp_client.shutil, "which", lambda name, path=None: which, raising=False
        )
        monkeypatch.setattr(acp_client, "augmented_path", lambda p: p or "/usr/bin", raising=False)
        monkeypatch.setattr(acp_client, "_opencode_acp_argv_cache", acp_client._UNRESOLVED)
        return acp_client._resolve_opencode_acp_bin()

    def test_env_override_wins_with_acp_subcommand(self, monkeypatch, tmp_path):
        binary = self._fake_binary(tmp_path, "override-opencode")
        argv, _searched = self._resolve(monkeypatch, tmp_path, override=binary)
        assert argv == [binary, "acp"]

    def test_mise_is_second(self, monkeypatch, tmp_path):
        mise_bin = self._fake_binary(tmp_path, "mise-opencode")
        argv, _searched = self._resolve(monkeypatch, tmp_path, mise=mise_bin)
        assert argv == [mise_bin, "acp"]

    def test_augmented_path_is_third(self, monkeypatch, tmp_path):
        path_bin = self._fake_binary(tmp_path, "path-opencode")
        argv, _searched = self._resolve(monkeypatch, tmp_path, which=path_bin)
        assert argv == [path_bin, "acp"]

    def test_not_found_reports_the_searched_path(self, monkeypatch, tmp_path):
        argv, searched = self._resolve(monkeypatch, tmp_path)
        assert argv is None
        assert searched  # the PATH that was searched comes back WITH the miss

    def test_spawn_arm_keeps_its_own_branch(self):
        """H9/H10 twin: opencode resolves its own binary and declares its own
        handshake literal; falling through to the kiro branch would spawn
        kiro-cli under an opencode label."""
        spawn_source = inspect.getsource(acp_client.AcpClient._spawn)
        assert "_is_opencode" in spawn_source
        assert "_resolve_opencode_acp_bin" in spawn_source
        assert acp_client.PROTOCOL_VERSION_OPENCODE is not None
        assert "PROTOCOL_VERSION_OPENCODE" in inspect.getsource(
            acp_client.AcpClient._initialize_session
        )
        assert acp_client.PROTOCOL_VERSION_OPENCODE != acp_client.PROTOCOL_VERSION

    def test_the_override_env_var_is_the_documented_spelling(self):
        assert acp_client._ENV_OPENCODE_ACP_BIN == "OPENCODE_ACP_BIN"


# ── 3. Probe state ──


@pytest.fixture(autouse=True)
def _clean_probe_cache():
    probe.clear_probe_cache()
    yield
    probe.clear_probe_cache()


class TestProbeState:
    """``_probe_opencode`` reads its verdict through the SPAWN's resolver."""

    def _stub(self, monkeypatch, value):
        monkeypatch.setattr(acp_client, "_resolve_opencode_acp_bin", lambda: value)

    def test_resolving_binary_reports_installed(self, monkeypatch):
        self._stub(monkeypatch, (["/usr/local/bin/opencode", "acp"], "/usr/bin"))
        state = probe.probe_backend(ACP_BACKEND_OPENCODE)
        assert state.installed == probe.INSTALLED
        assert state.missing_components == ()
        # No remedy beside an "installed" row: an outstanding-looking command
        # on a working backend is an action that is already done.
        assert state.install_command == ""

    def test_absent_binary_reports_missing_and_names_the_auth_prerequisite(self, monkeypatch):
        self._stub(monkeypatch, (None, "/usr/bin"))
        state = probe.probe_backend(ACP_BACKEND_OPENCODE)
        assert state.installed == probe.MISSING
        assert state.missing_components == (probe.COMPONENT_OPENCODE,)
        # Installing alone does not make a session work: the harness signs in
        # from its own store, so the remedy names ``opencode auth login`` too.
        assert "opencode auth login" in state.install_command
        assert state.policy_id == "opencode"

    def test_cached_negative_marks_restart_required(self, monkeypatch):
        self._stub(monkeypatch, (["/usr/local/bin/opencode", "acp"], "/usr/bin"))
        monkeypatch.setattr(acp_client, "_opencode_acp_argv_cache", (None, "/usr/bin"))
        state = probe.probe_backend(ACP_BACKEND_OPENCODE)
        assert state.installed == probe.INSTALLED
        assert state.restart_required is True

    def test_unresolved_cache_is_not_a_negative(self, monkeypatch):
        self._stub(monkeypatch, (["/usr/local/bin/opencode", "acp"], "/usr/bin"))
        monkeypatch.setattr(acp_client, "_opencode_acp_argv_cache", acp_client._UNRESOLVED)
        state = probe.probe_backend(ACP_BACKEND_OPENCODE)
        assert state.restart_required is False

    def test_driver_seam_reads_the_spawn_resolver(self, monkeypatch):
        from kiro_crew.agent_sdk.drivers import acp as driver

        monkeypatch.setattr(
            acp_client, "_resolve_opencode_acp_bin", lambda: (["/o/opencode", "acp"], "/p")
        )
        assert driver.opencode_resolves() is True
        monkeypatch.setattr(acp_client, "_resolve_opencode_acp_bin", lambda: (None, "/p"))
        assert driver.opencode_resolves() is False

    def test_probe_listing_covers_opencode(self, monkeypatch):
        monkeypatch.setattr(
            acp_client, "_resolve_kiro_bin", lambda **_kw: "/usr/local/bin/kiro-cli"
        )
        monkeypatch.setattr(acp_client, "_resolve_claude_acp_bin", lambda: (None, "/usr/bin"))
        monkeypatch.setattr(acp_client, "_resolve_claude_code_executable", lambda: None)
        monkeypatch.setattr(acp_client, "_resolve_codex_acp_bin", lambda: (None, "/usr/bin"))
        monkeypatch.setattr(acp_client, "_resolve_opencode_acp_bin", lambda: (None, "/usr/bin"))
        states = {s.backend: s for s in probe.probe_backends()}
        assert set(states) == set(ACP_BACKENDS_KNOWN)
        assert states[ACP_BACKEND_OPENCODE].installed == probe.MISSING


# ── 4. Capability gating ──


class TestCapabilityGating:
    """A gated feature requested on opencode fails fast, with the reason."""

    def test_steer_is_refused_without_touching_the_wire(self, tmp_path):
        client = _client(tmp_path)
        client._session_id = "sid"
        client._send_request = AsyncMock()
        assert client.supports_steer is False
        import asyncio

        assert asyncio.run(client.steer("halfway there")) is False
        client._send_request.assert_not_awaited()

    def test_manual_compact_is_refused_up_front(self, tmp_path):
        """The #7800 gate: a backend that never emits compaction status must be
        refused by the manual entry points, not left stranding the waiter."""
        from types import SimpleNamespace

        from kiro_crew.providers import acp as providers_acp

        client = _client(tmp_path)
        fake_provider = SimpleNamespace(_client=client)
        verdict = providers_acp.AcpProvider.manual_compact_unsupported_backend.fget(fake_provider)
        assert verdict == ACP_BACKEND_OPENCODE

    def test_explicit_model_switch_raises_instead_of_sending(self, tmp_path):
        import asyncio

        client = _client(tmp_path)
        client._session_id = "sid"
        client._send_request = AsyncMock()
        with pytest.raises(acp_client.AcpError, match="[Mm]odel switching"):
            asyncio.run(client.set_model("some-model"))
        client._send_request.assert_not_awaited()

    def test_startup_model_pin_is_withheld_not_sent(self, tmp_path):
        """The startup path withholds (the pin was not chosen for this turn)
        where the explicit switch raises -- the same split kiro's entitlement
        withhold makes against its own set_model."""
        import asyncio

        client = _client(tmp_path)
        client._session_id = "sid"
        client._model = "some-model"
        client._send_request = AsyncMock()
        asyncio.run(client._apply_startup_model())
        client._send_request.assert_not_awaited()
        assert client._model == acp_client.DEFAULT_MODEL


# ── 5. Serialization ──


class TestSerialization:
    """The wire shape: the shared prompt serializer, and no opencode MCP seam."""

    def test_no_opencode_mcp_seam_exists(self):
        """The seam the codex twin kept was removed rather than left dormant:
        opencode is not selectable, so there is no session of its own to splice
        servers into, and an always-empty hook would only invite an ungated
        splice. The per-harness splices that remain at both session-creation
        sites are claude's and codex's."""
        for fn in (
            acp_client.AcpClient._new_session_following_substitution,
            acp_client.AcpClient._initialize_session,
        ):
            source = inspect.getsource(fn)
            assert "_opencode_session_mcp_servers" not in source, (
                f"{fn.__name__}: the opencode MCP seam was removed; re-adding it "
                f"is a splice decision that needs wire evidence and selectability"
            )
            assert "if self._is_codex" in source
            assert "if self._is_claude" in source

    def test_resume_carries_no_crew_side_session_file(self):
        """opencode keeps its own session records, so the codex resume shape
        applies: no kiro transcript path, no _meta of ours."""
        source = inspect.getsource(acp_client.AcpClient._initialize_session)
        assert "elif self._is_opencode" in source

    def test_prompts_use_the_shared_serializer(self, tmp_path):
        """Text / image / embedded-context blocks are built by the SAME
        ``build_prompt_blocks`` every backend uses -- opencode gets no private
        prompt shape, so a serializer fix lands on it automatically."""
        source = inspect.getsource(acp_client.AcpClient._send_prompt)
        assert "build_prompt_blocks" in source


# ── 6. Member-dispatch and mirror exclusion ──


class TestExclusions:
    """The fail-safe direction, stated positively."""

    def test_member_dispatch_does_not_mount_for_opencode(self, tmp_path):
        client = _client(tmp_path)
        servers = [{"name": "spec-server"}]
        assert client._append_member_dispatch_server(servers) is servers

    def test_no_mirror_with_a_stated_reason(self):
        """``mirror_for`` must ANSWER for opencode (declared no-mirror), never
        raise -- an unregistered backend is the failure the mirror registry
        exists to catch."""
        from kiro_crew.providers.mirrors import mirror_for

        assert mirror_for(ACP_BACKEND_OPENCODE) is None

    def test_provider_label_is_its_own(self):
        """H11 twin: the label is what keeps an opencode session out of the kiro
        namespace (resume compatibility, session-map persistence, cleanup)."""
        from kiro_crew.providers import acp as providers_acp

        client = MagicMock()
        client.backend = ACP_BACKEND_OPENCODE
        provider = MagicMock(spec=providers_acp.AcpProvider)
        provider.client = client
        assert providers_acp.provider_label(provider) == PROVIDER_LABEL_OPENCODE
        assert PROVIDER_LABEL_OPENCODE not in (
            PROVIDER_LABEL_DEFAULT,
            PROVIDER_LABEL_CODEX,
        )

    def test_tool_gate_is_honestly_unverified_not_fake_routed(self):
        """v1 routes no Crew tools to opencode (nothing is mounted), so the
        gate has nothing to arm -- and the routing table must SAY that
        (UNVERIFIED → INDETERMINATE) rather than borrow codex's
        SESSION_CONFIG verdict. Task-6 QE review reads this state."""
        from kiro_crew import acp_tool_gate

        assert acp_tool_gate.routing_for(ACP_BACKEND_OPENCODE) is (acp_tool_gate.Routing.UNVERIFIED)
        verdict, _reason = acp_tool_gate.routing_verdict(ACP_BACKEND_OPENCODE)
        assert verdict is acp_tool_gate.Verdict.INDETERMINATE
        assert acp_tool_gate.is_enforced(ACP_BACKEND_OPENCODE) is False
        # No enforced harness, no compensating credential mask to derive --
        # and none may appear without an enforcement decision first.
        assert acp_tool_gate.adapter_hidden_credential_dirs(ACP_BACKEND_OPENCODE) == ()
        assert acp_tool_gate.label_for(ACP_BACKEND_OPENCODE) == "OpenCode"
