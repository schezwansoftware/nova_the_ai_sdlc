"""TUI integration tests (cli/tui.py's NovaApp).

Same spirit as test_cli_contract.py: drive real code (the actual `NovaApp`,
not a mock) against a real `run_platform_server` instance -- no mocking of
the HTTP boundary. Interaction is driven through `textual`'s own headless
test harness (`App.run_test()` / `Pilot`), simulating real keypresses into
the input box rather than calling internal methods directly, since the
point of this module is that a human types into one box for the whole
session.
"""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from ai_sdlc.cli import bootstrap
from ai_sdlc.cli.client import PlatformClient
from ai_sdlc.cli.config import CLIConfig
from ai_sdlc.cli.tui import NovaApp
from ai_sdlc.platform.server import run_platform_server

_REQUIREMENT_TEXT = (
    "Add support for Redis caching to our order service to reduce DB load "
    "under high traffic. The system must respond within 50ms for cached hits."
)
# Contains "tbd" -- POAgent._VAGUENESS_MARKERS -- so the real (mock-backed)
# PO Agent raises a genuine NEEDS_CLARIFICATION, the same trigger used by
# test_workflow_full_sequence.py's clarification-round-trip test.
_AMBIGUOUS_REQUIREMENT_TEXT = "The scope here is tbd, figure out the details later on your own."


def _start_server(workspace: Path):
    server = run_platform_server(str(workspace), host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


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


def _config(workspace: Path, port: int) -> CLIConfig:
    return CLIConfig(
        workspace=str(workspace),
        host="127.0.0.1",
        port=port,
        initiator_id="u1",
        current_workflow_id=None,
        agent_framework=None,
    )


async def _wait_until(condition, pilot, attempts: int = 50, interval: float = 0.1) -> None:
    for _ in range(attempts):
        if condition():
            return
        await pilot.pause(interval)
    raise AssertionError("condition was not met in time")


def _transcript_text(app: NovaApp) -> str:
    log = app.query_one("#transcript")
    return "\n".join(str(line) for line in log.lines)


def test_tui_happy_path_completes_workflow(real_agents_server) -> None:
    workspace, port = real_agents_server
    config = _config(workspace, port)
    client = PlatformClient(host="127.0.0.1", port=port)
    app = NovaApp(config=config, client=client, initial_prompt=_REQUIREMENT_TEXT)

    async def _run() -> None:
        async with app.run_test(size=(100, 40)) as pilot:
            await _wait_until(lambda: app._reached_halt, pilot)

            assert app.workflow_id is not None
            assert app.left_workflow_paused is False
            text = _transcript_text(app)
            assert f"Started workflow {app.workflow_id}" in text
            assert "Workflow completed." in text
            assert "REQUIREMENTS" in text and "ARCHITECTURE" in text and "UX_DESIGN" in text

    asyncio.run(_run())


def test_tui_prompts_for_requirement_and_resolves_clarification(real_agents_server) -> None:
    workspace, port = real_agents_server
    config = _config(workspace, port)
    client = PlatformClient(host="127.0.0.1", port=port)
    app = NovaApp(config=config, client=client, initial_prompt=None)

    async def _run() -> None:
        async with app.run_test(size=(100, 40)) as pilot:
            assert app._awaiting == ("requirement", "")

            await pilot.click("#input")
            for ch in _AMBIGUOUS_REQUIREMENT_TEXT:
                await pilot.press(ch)
            await pilot.press("enter")

            await _wait_until(lambda: app._awaiting is not None and app._awaiting[0] == "clarification", pilot)
            assert "Clarification requested" in _transcript_text(app)

            for ch in "This covers new customers signing up through the web app only.":
                await pilot.press(ch)
            await pilot.press("enter")

            await _wait_until(lambda: app._reached_halt, pilot)
            text = _transcript_text(app)
            assert "Clarification accepted" in text
            assert "Workflow completed." in text

    asyncio.run(_run())


def test_tui_quit_mid_clarification_leaves_workflow_paused_and_resumable(real_agents_server) -> None:
    workspace, port = real_agents_server
    config = _config(workspace, port)
    client = PlatformClient(host="127.0.0.1", port=port)
    app = NovaApp(config=config, client=client, initial_prompt=_AMBIGUOUS_REQUIREMENT_TEXT)

    async def _run() -> None:
        async with app.run_test(size=(100, 40)) as pilot:
            await _wait_until(lambda: app._awaiting is not None and app._awaiting[0] == "clarification", pilot)
            assert app.workflow_id is not None

            await pilot.press("ctrl+c")
            await pilot.pause(0.1)

    asyncio.run(_run())

    workflow_id = app.workflow_id
    assert workflow_id is not None
    assert app.left_workflow_paused is True
    assert app._reached_halt is False

    # Resumable from outside the TUI, exactly like a real Ctrl-C -- proves
    # quitting mid-clarification didn't corrupt server-side workflow state.
    status = client.get_status(workflow_id)
    assert status.pending_action is not None
    assert status.pending_action.action_type == "CLARIFICATION"

    data = client.submit_clarification(
        workflow_id, config.initiator_id, status.pending_action.interaction_id, "Web signup flow only."
    )
    assert "resuming" in data.message.lower() or "accepted" in data.message.lower()

    final_status = client.get_status(workflow_id)
    assert final_status.status == "COMPLETED"
