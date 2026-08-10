"""CLI contract/integration tests.

These drive the real `ai-sdlc` Typer app (`ai_sdlc.cli.main.app`) through
`typer.testing.CliRunner`, against a real `run_platform_server` instance
(the same Core Platform HTTP API a human would run) -- no mocking of the
HTTP boundary the CLI is supposed to prove works. Agent registry discovery
uses the CLI's own `bootstrap.write_agent_metadata` scaffolding, so these
tests also prove `ai-sdlc init`'s scaffolding is what real agents need.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_sdlc.cli import bootstrap
from ai_sdlc.cli.config import CLIConfig, load_config, save_config
from ai_sdlc.cli.main import app
from ai_sdlc.platform.server import run_platform_server

runner = CliRunner()


def _flatten(output: str) -> str:
    """Rich wraps long lines (workflow ids, long ids embedded in messages)
    at the runner's default ~80-column width, splitting a single token
    across a line break. Since wrapping never drops characters, joining on
    whitespace reconstructs any unbroken token for substring assertions."""
    return "".join(output.split())


_REQUIREMENT_TEXT = (
    "Add support for Redis caching to our order service to reduce DB load "
    "under high traffic. The system must respond within 50ms for cached hits."
)


def _start_server(workspace: Path):
    server = run_platform_server(str(workspace), host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


@pytest.fixture
def cli_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config_dir = tmp_path / "cli-config"
    monkeypatch.setenv("AI_SDLC_CLI_CONFIG_DIR", str(config_dir))
    return config_dir


def _write_config(config_dir: Path, workspace: Path, port: int, initiator_id: str = "u1") -> None:
    save_config(
        CLIConfig(
            workspace=str(workspace),
            host="127.0.0.1",
            port=port,
            initiator_id=initiator_id,
            current_workflow_id=None,
        )
    )


@pytest.fixture
def real_agents_server(tmp_path: Path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    bootstrap.write_agent_metadata(workspace)
    server, thread = _start_server(workspace)
    try:
        yield workspace, server.server_address[1]
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_version_flag_prints_version_and_exits(tmp_path: Path, cli_config_dir: Path) -> None:
    from ai_sdlc.cli.version import CLI_VERSION

    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert CLI_VERSION in result.output


def test_help_lists_all_commands_and_description(tmp_path: Path, cli_config_dir: Path) -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    for command in ("init", "start", "status", "answer", "approve", "reject", "cancel"):
        assert command in result.output
    assert "interactive CLI" in result.output


def test_init_scaffolds_config_and_agent_metadata_and_detects_missing_server(
    tmp_path: Path, cli_config_dir: Path
) -> None:
    workspace = tmp_path / "repo"

    result = runner.invoke(
        app,
        ["init", "--workspace", str(workspace), "--host", "127.0.0.1", "--port", "8199"],
    )

    assert result.exit_code == 0, result.output
    assert "Scaffolded agent registry metadata" in result.output
    assert "No Core Platform API detected" in result.output

    config_path = cli_config_dir / "config.json"
    assert config_path.exists()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["workspace"] == str(workspace.resolve())
    assert config["port"] == 8199

    agents_dir = workspace / ".ai-sdlc" / "agents"
    written_ids = {json.loads(p.read_text(encoding="utf-8"))["agent_id"] for p in agents_dir.glob("*.json")}
    assert written_ids == {"po", "architecture", "ux"}


def test_init_is_idempotent_and_detects_already_running_server(
    tmp_path: Path, cli_config_dir: Path
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    bootstrap.write_agent_metadata(workspace)
    server, thread = _start_server(workspace)
    try:
        port = server.server_address[1]
        result = runner.invoke(app, ["init", "--workspace", str(workspace), "--port", str(port)])
        assert result.exit_code == 0, result.output
        assert "already present; left untouched" in result.output
        assert "already running" in result.output
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_init_start_server_spawns_and_waits_for_reachability(tmp_path: Path, cli_config_dir: Path) -> None:
    workspace = tmp_path / "repo"

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    result = runner.invoke(
        app,
        ["init", "--workspace", str(workspace), "--host", "127.0.0.1", "--port", str(port), "--start-server"],
    )
    try:
        assert result.exit_code == 0, result.output
        assert "Started Core Platform API" in result.output

        status_result = runner.invoke(app, ["start", "--prompt", _REQUIREMENT_TEXT])
        assert status_result.exit_code == 0, status_result.output
        assert "COMPLETED" in status_result.output or "Workflow completed" in status_result.output
    finally:
        subprocess.run(["pkill", "-f", f"ai_sdlc.platform.server.*--port {port}"])


def test_status_without_init_reports_clear_error(tmp_path: Path, cli_config_dir: Path) -> None:
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 1
    assert "ai-sdlc init" in result.output


def test_start_without_reachable_server_reports_connection_guidance(
    tmp_path: Path, cli_config_dir: Path
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _write_config(cli_config_dir, workspace, port=8299)

    result = runner.invoke(app, ["start", "--prompt", _REQUIREMENT_TEXT])
    assert result.exit_code == 1
    assert "Could not reach the Core Platform API" in result.output
    assert "python -m ai_sdlc.platform.server" in result.output


def test_happy_path_start_reaches_completed(real_agents_server, cli_config_dir: Path) -> None:
    workspace, port = real_agents_server
    _write_config(cli_config_dir, workspace, port=port)

    start_result = runner.invoke(app, ["start", "--prompt", _REQUIREMENT_TEXT])
    assert start_result.exit_code == 0, start_result.output
    assert "REQUIREMENTS" in start_result.output
    assert "ARCHITECTURE" in start_result.output
    assert "UX_DESIGN" in start_result.output
    assert "Workflow completed" in start_result.output

    status_result = runner.invoke(app, ["status"])
    assert status_result.exit_code == 0, status_result.output
    assert "Workflow completed" in status_result.output


def test_start_without_prompt_asks_interactively_and_rejects_too_short_text(
    real_agents_server, cli_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, port = real_agents_server
    _write_config(cli_config_dir, workspace, port=port)
    monkeypatch.setattr("ai_sdlc.cli.handlers._is_interactive_session", lambda: True)

    result = runner.invoke(app, ["start"], input=f"too short\n{_REQUIREMENT_TEXT}\n")

    assert result.exit_code == 0, result.output
    assert "ai-sdlc" in result.output  # banner
    assert "Define your requirement" in result.output
    assert "needs to be at least" in result.output  # re-prompted after "too short"
    assert "Workflow completed" in result.output


def test_start_without_prompt_reads_requirement_from_file_path(
    real_agents_server, cli_config_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace, port = real_agents_server
    _write_config(cli_config_dir, workspace, port=port)
    monkeypatch.setattr("ai_sdlc.cli.handlers._is_interactive_session", lambda: True)

    requirement_file = tmp_path / "requirement.txt"
    requirement_file.write_text(_REQUIREMENT_TEXT, encoding="utf-8")

    result = runner.invoke(app, ["start"], input=f"{requirement_file}\n")

    assert result.exit_code == 0, result.output
    assert "Read requirement from" in result.output
    assert "Workflow completed" in result.output


def test_start_without_prompt_non_interactive_fails_without_blocking(
    tmp_path: Path, cli_config_dir: Path
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _write_config(cli_config_dir, workspace, port=8299)

    result = runner.invoke(app, ["start"])

    assert result.exit_code == 1
    assert "isn't interactive" in result.output
    assert '--prompt "<requirement>"' in result.output


def test_clarification_interrupt_answer_resumes_to_completion(clarification_stub_server, cli_config_dir: Path) -> None:
    workspace, port = clarification_stub_server
    _write_config(cli_config_dir, workspace, port=port)

    start_result = runner.invoke(app, ["start", "--prompt", _REQUIREMENT_TEXT])
    assert start_result.exit_code == 0, start_result.output
    assert "Clarification requested" in start_result.output

    status_result = runner.invoke(app, ["status"])
    assert status_result.exit_code == 0, status_result.output
    assert "Clarification requested" in status_result.output

    answer_result = runner.invoke(app, ["answer", "Use a modular monolith with a dedicated cache layer."])
    assert answer_result.exit_code == 0, answer_result.output
    assert "Workflow completed" in answer_result.output


def test_interactive_start_resolves_clarification_inline(
    clarification_stub_server, cli_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In a real terminal session, `start` should prompt for the answer
    itself and drive the workflow to completion in one process -- no
    separate `ai-sdlc answer` invocation needed (docs §12.1)."""
    workspace, port = clarification_stub_server
    _write_config(cli_config_dir, workspace, port=port)
    monkeypatch.setattr("ai_sdlc.cli.handlers._is_interactive_session", lambda: True)

    result = runner.invoke(
        app,
        ["start", "--prompt", _REQUIREMENT_TEXT],
        input="Use a modular monolith with a dedicated cache layer.\n",
    )

    assert result.exit_code == 0, result.output
    assert "Clarification requested" in result.output
    assert "Your answer" in result.output
    assert "Workflow completed" in result.output


def test_interactive_start_resolves_approval_approve_inline(
    approval_stub_server, cli_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, port = approval_stub_server
    _write_config(cli_config_dir, workspace, port=port)
    monkeypatch.setattr("ai_sdlc.cli.handlers._is_interactive_session", lambda: True)

    result = runner.invoke(app, ["start", "--prompt", _REQUIREMENT_TEXT], input="y\n")

    assert result.exit_code == 0, result.output
    assert "Approval requested" in result.output
    assert "Workflow completed" in result.output


def test_interactive_start_resolves_approval_reject_inline_and_halts(
    approval_stub_server, cli_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, port = approval_stub_server
    _write_config(cli_config_dir, workspace, port=port)
    monkeypatch.setattr("ai_sdlc.cli.handlers._is_interactive_session", lambda: True)

    result = runner.invoke(
        app,
        ["start", "--prompt", _REQUIREMENT_TEXT],
        input="n\nNeeds another pass on accessibility.\n",
    )

    assert result.exit_code == 0, result.output
    assert "Approval requested" in result.output
    assert "requires revision" in result.output
    assert "needs revision" in result.output


def test_interactive_start_non_tty_stops_at_first_pending_action(
    clarification_stub_server, cli_config_dir: Path
) -> None:
    """Without a TTY (e.g. CI), `start` must not block waiting on input --
    it should stop at the first pending action and point at the scriptable
    escape hatches instead."""
    workspace, port = clarification_stub_server
    _write_config(cli_config_dir, workspace, port=port)

    result = runner.invoke(app, ["start", "--prompt", _REQUIREMENT_TEXT])

    assert result.exit_code == 0, result.output
    assert "Clarification requested" in result.output
    assert "Non-interactive session" in result.output
    assert "ai-sdlc answer" in result.output


def test_interactive_start_keyboard_interrupt_leaves_workflow_paused(
    clarification_stub_server, cli_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, port = clarification_stub_server
    _write_config(cli_config_dir, workspace, port=port)
    monkeypatch.setattr("ai_sdlc.cli.handlers._is_interactive_session", lambda: True)

    def _raise_interrupt(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr("rich.console.Console.input", _raise_interrupt)

    result = runner.invoke(app, ["start", "--prompt", _REQUIREMENT_TEXT])

    assert result.exit_code == 0, result.output
    assert "Interrupted" in result.output
    assert "paused" in result.output

    status_result = runner.invoke(app, ["status"])
    assert status_result.exit_code == 0, status_result.output
    assert "Clarification requested" in status_result.output

    answer_result = runner.invoke(app, ["answer", "Use a modular monolith with a dedicated cache layer."])
    assert answer_result.exit_code == 0, answer_result.output
    assert "Workflow completed" in answer_result.output


def test_answer_with_no_pending_clarification_errors_clearly(real_agents_server, cli_config_dir: Path) -> None:
    workspace, port = real_agents_server
    _write_config(cli_config_dir, workspace, port=port)

    start_result = runner.invoke(app, ["start", "--prompt", _REQUIREMENT_TEXT])
    assert start_result.exit_code == 0, start_result.output

    answer_result = runner.invoke(app, ["answer", "irrelevant"])
    assert answer_result.exit_code == 1
    assert "No pending clarification" in answer_result.output


def _write_interrupt_once_stub_agent(
    tmp_path: Path, workspace: Path, agent_id: str, interrupt: str, module_name: str
) -> None:
    """Overwrite `<agent_id>.json`'s `impl` to point at a stub agent module
    (written to `tmp_path`, which the caller must put on `sys.path`) that
    requests a clarification/approval on its first call, then completes on
    its second. Deterministically exercises the answer/approve/reject
    resume paths without depending on any real agent's heuristics landing
    on that branch -- same technique as
    `tests/test_workflow_full_sequence.py::_InterruptOnceThenCompleteAgent`,
    adapted for HTTP+registry discovery instead of direct Python
    registration (the CLI only ever reaches agents over HTTP).

    NOTE ON WHY THIS STUB EXISTS AT ALL FOR CLARIFICATION: the real
    POAgent's clarification-resume path was tried first and found to be
    broken for a workflow's *first* node reached through the public API --
    see the "PO Agent clarification-resume bug" note in this repo's
    todo.md. That bug is out of CLI scope to fix, so this stub proves the
    CLI's `answer` mechanics work correctly against a well-behaved backend
    node instead.
    """
    assert interrupt in ("clarification", "approval")
    stub_module = tmp_path / f"{module_name}.py"
    if interrupt == "clarification":
        interrupt_result = (
            "AgentResult(\n"
            "                request_id=request.request_id,\n"
            "                workflow_id=request.workflow_id,\n"
            f"                agent_id={agent_id!r},\n"
            "                status=AgentStatus.NEEDS_CLARIFICATION,\n"
            f"                questions=[{agent_id!r} + ': please clarify before proceeding.'],\n"
            "            )"
        )
    else:
        interrupt_result = (
            "AgentResult(\n"
            "                request_id=request.request_id,\n"
            "                workflow_id=request.workflow_id,\n"
            f"                agent_id={agent_id!r},\n"
            "                status=AgentStatus.NEEDS_APPROVAL,\n"
            f"                artifact=ArtifactRef(type={agent_id!r}, path='.ai-sdlc/{agent_id}.json'),\n"
            "                decision=AgentDecision(status='ready_for_approval', approval_required=True),\n"
            "            )"
        )
    stub_module.write_text(
        "from ai_sdlc.agents.base import AgentDecision, AgentResult, AgentStatus, ArtifactRef\n"
        "\n"
        "class InterruptOnceThenCompleteAgent:\n"
        f"    agent_id = {agent_id!r}\n"
        "\n"
        "    def __init__(self):\n"
        "        self.calls = 0\n"
        "\n"
        "    def execute(self, request):\n"
        "        self.calls += 1\n"
        "        if self.calls == 1:\n"
        f"            return {interrupt_result}\n"
        "        return AgentResult(\n"
        "            request_id=request.request_id,\n"
        "            workflow_id=request.workflow_id,\n"
        f"            agent_id={agent_id!r},\n"
        "            status=AgentStatus.COMPLETED,\n"
        f"            data={{'stage': {agent_id!r}, 'resolved': True}},\n"
        "        )\n",
        encoding="utf-8",
    )

    metadata_path = workspace / ".ai-sdlc" / "agents" / f"{agent_id}.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["impl"] = f"{module_name}.InterruptOnceThenCompleteAgent"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")


@pytest.fixture
def clarification_stub_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Real po agent, but a stub `architecture` agent that requests
    clarification on its first call and completes on its second."""
    workspace = tmp_path / "repo"
    workspace.mkdir()
    bootstrap.write_agent_metadata(workspace)
    _write_interrupt_once_stub_agent(
        tmp_path, workspace, "architecture", "clarification", "cli_clarification_stub_agent"
    )

    monkeypatch.syspath_prepend(str(tmp_path))
    sys.path.insert(0, str(tmp_path))
    try:
        server, thread = _start_server(workspace)
        try:
            yield workspace, server.server_address[1]
        finally:
            server.shutdown()
            thread.join(timeout=5)
    finally:
        if str(tmp_path) in sys.path:
            sys.path.remove(str(tmp_path))


@pytest.fixture
def approval_stub_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Real po/architecture agents, but a stub `ux` agent that requests
    approval on its first call and completes on its second -- exercises
    the approve/reject paths the real (mock-provider-backed) UX agent
    doesn't naturally take."""
    workspace = tmp_path / "repo"
    workspace.mkdir()
    bootstrap.write_agent_metadata(workspace)
    _write_interrupt_once_stub_agent(tmp_path, workspace, "ux", "approval", "cli_approval_stub_agent")

    monkeypatch.syspath_prepend(str(tmp_path))
    sys.path.insert(0, str(tmp_path))
    try:
        server, thread = _start_server(workspace)
        try:
            yield workspace, server.server_address[1]
        finally:
            server.shutdown()
            thread.join(timeout=5)
    finally:
        if str(tmp_path) in sys.path:
            sys.path.remove(str(tmp_path))


def test_approval_interrupt_approve_resumes_to_completion(approval_stub_server, cli_config_dir: Path) -> None:
    workspace, port = approval_stub_server
    _write_config(cli_config_dir, workspace, port=port)

    start_result = runner.invoke(app, ["start", "--prompt", _REQUIREMENT_TEXT])
    assert start_result.exit_code == 0, start_result.output
    assert "Approval requested" in start_result.output

    approve_result = runner.invoke(app, ["approve"])
    assert approve_result.exit_code == 0, approve_result.output
    assert "Workflow completed" in approve_result.output


def test_approval_interrupt_reject_reports_revision_required(approval_stub_server, cli_config_dir: Path) -> None:
    workspace, port = approval_stub_server
    _write_config(cli_config_dir, workspace, port=port)

    start_result = runner.invoke(app, ["start", "--prompt", _REQUIREMENT_TEXT])
    assert start_result.exit_code == 0, start_result.output
    assert "Approval requested" in start_result.output

    reject_result = runner.invoke(app, ["reject", "--reason", "Needs another pass on accessibility."])
    assert reject_result.exit_code == 0, reject_result.output
    assert "requires revision" in reject_result.output

    status_result = runner.invoke(app, ["status"])
    assert status_result.exit_code == 0, status_result.output
    assert "revision_required" in status_result.output


def test_reject_requires_reason_option(real_agents_server, cli_config_dir: Path) -> None:
    workspace, port = real_agents_server
    _write_config(cli_config_dir, workspace, port=port)
    runner.invoke(app, ["start", "--prompt", _REQUIREMENT_TEXT])

    result = runner.invoke(app, ["reject"])
    assert result.exit_code != 0


def test_cancel_is_terminal(approval_stub_server, cli_config_dir: Path) -> None:
    # A workflow that's mid-flight (WAITING_FOR_APPROVAL, not yet COMPLETED)
    # so the first cancel has a non-terminal state to actually act on.
    workspace, port = approval_stub_server
    _write_config(cli_config_dir, workspace, port=port)

    start_result = runner.invoke(app, ["start", "--prompt", _REQUIREMENT_TEXT])
    assert start_result.exit_code == 0, start_result.output
    assert "Approval requested" in start_result.output

    cancel_result = runner.invoke(app, ["cancel", "--reason", "no longer needed"])
    assert cancel_result.exit_code == 0, cancel_result.output
    assert "cancelled" in cancel_result.output

    second_cancel = runner.invoke(app, ["cancel", "--reason", "again"])
    assert second_cancel.exit_code == 1
    assert "INVALID_STATE_TRANSITION" in second_cancel.output


def test_workflow_id_override_targets_a_specific_workflow(real_agents_server, cli_config_dir: Path) -> None:
    workspace, port = real_agents_server
    _write_config(cli_config_dir, workspace, port=port)

    first = runner.invoke(app, ["start", "--prompt", _REQUIREMENT_TEXT])
    assert first.exit_code == 0, first.output
    first_workflow_id = load_config().current_workflow_id

    second = runner.invoke(app, ["start", "--prompt", "Add CSV import support for vendor onboarding data."])
    assert second.exit_code == 0, second.output
    second_workflow_id = load_config().current_workflow_id
    assert first_workflow_id != second_workflow_id

    # `status` with no override targets the "current" (second) workflow...
    status_default = runner.invoke(app, ["status"])
    assert status_default.exit_code == 0
    assert second_workflow_id in _flatten(status_default.output)

    # ...but --workflow-id can still reach back to the first, without
    # disturbing which workflow is "current".
    status_first = runner.invoke(app, ["status", "--workflow-id", first_workflow_id])
    assert status_first.exit_code == 0, status_first.output
    assert first_workflow_id in _flatten(status_first.output)

    # "current" is still the second workflow, unaffected by the override.
    assert load_config().current_workflow_id == second_workflow_id
