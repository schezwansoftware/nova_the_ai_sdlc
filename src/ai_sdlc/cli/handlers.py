"""Command implementations. `main.py` wires typer options/arguments to these
functions; keeping them separate makes the command logic directly testable
without going through typer's CLI-parsing layer."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

import typer
from rich.console import Console

from ai_sdlc.cli import bootstrap, formatters
from ai_sdlc.cli.client import APIError, ConnectionUnavailable, PlatformClient
from ai_sdlc.cli.config import CLIConfig, config_path, load_config, save_config


def _require_config(console: Console) -> CLIConfig:
    config = load_config()
    if config is None:
        formatters.render_error(console, "No ai-sdlc CLI configuration found.")
        console.print("Run [bold]ai-sdlc init[/bold] first.")
        raise typer.Exit(code=1)
    return config


def _client_for(config: CLIConfig) -> PlatformClient:
    return PlatformClient(host=config.host, port=config.port)


def _server_start_hint(config: CLIConfig) -> str:
    return (
        f"Start the Core Platform API with:\n"
        f"  python -m ai_sdlc.platform.server {config.workspace} --host {config.host} --port {config.port}\n"
        f"or re-run:\n"
        f"  ai-sdlc init --workspace {config.workspace} --host {config.host} --port {config.port} --start-server"
    )


def _fail_connection(console: Console, config: CLIConfig, exc: ConnectionUnavailable) -> None:
    formatters.render_error(console, str(exc))
    console.print(_server_start_hint(config))
    raise typer.Exit(code=1)


def _fail_api(console: Console, exc: APIError) -> None:
    formatters.render_error(console, f"{exc.code}: {exc.message}")
    raise typer.Exit(code=1)


def _require_workflow_id(console: Console, config: CLIConfig, override: Optional[str]) -> str:
    workflow_id = override or config.current_workflow_id
    if not workflow_id:
        formatters.render_error(console, "No active workflow.")
        console.print('Run [bold]ai-sdlc start --prompt "<requirement>"[/bold] first, or pass --workflow-id.')
        raise typer.Exit(code=1)
    return workflow_id


def _is_interactive_session() -> bool:
    """Separate seam from the `sys.stdin.isatty()` call itself so tests can
    force the interactive branch without needing a real TTY."""
    return sys.stdin.isatty()


def _refresh_and_render(console: Console, client: PlatformClient, workflow_id: str) -> None:
    try:
        status = client.get_status(workflow_id)
    except (ConnectionUnavailable, APIError):
        # The mutating call already succeeded and printed its own result;
        # a follow-up status refresh failing is worth a note, not a hard
        # failure of the command that already succeeded.
        formatters.render_warning(console, "Workflow updated, but a follow-up status check failed.")
        return
    formatters.render_pipeline(console, status)


def run_init(
    console: Console,
    workspace: Path,
    host: str,
    port: int,
    initiator_id: Optional[str],
    start_server: bool,
) -> None:
    resolved_workspace = workspace.resolve()
    resolved_workspace.mkdir(parents=True, exist_ok=True)

    config = CLIConfig(
        workspace=str(resolved_workspace),
        host=host,
        port=port,
        initiator_id=initiator_id or bootstrap.default_initiator_id(),
        current_workflow_id=None,
    )
    save_config(config)
    formatters.render_success(console, f"CLI config written to {config_path()}.")

    written = bootstrap.write_agent_metadata(resolved_workspace)
    if written:
        formatters.render_success(console, "Scaffolded agent registry metadata:")
        for path in written:
            console.print(f"  {path}")
    else:
        console.print("[dim]Agent registry metadata already present; left untouched.[/dim]")

    client = _client_for(config)
    if client.is_reachable():
        formatters.render_success(console, f"Core Platform API already running at http://{host}:{port}.")
        return

    if start_server:
        process = bootstrap.spawn_server(resolved_workspace, host, port)
        if bootstrap.wait_until_reachable(client):
            formatters.render_success(
                console, f"Started Core Platform API (pid {process.pid}) at http://{host}:{port}."
            )
        else:
            formatters.render_warning(
                console,
                f"Started process (pid {process.pid}) but it did not become reachable at "
                f"http://{host}:{port} within the timeout; check for a port conflict or startup error.",
            )
        return

    formatters.render_warning(console, f"No Core Platform API detected at http://{host}:{port}.")
    console.print(_server_start_hint(config))


def run_start(console: Console, prompt: str, project_context: Optional[Dict[str, Any]] = None) -> None:
    config = _require_config(console)
    client = _client_for(config)
    try:
        data = client.start_workflow(config.initiator_id, prompt, project_context or {})
    except ConnectionUnavailable as exc:
        _fail_connection(console, config, exc)
        return
    except APIError as exc:
        _fail_api(console, exc)
        return

    config.current_workflow_id = data.workflow_id
    save_config(config)
    formatters.render_success(console, f"Started workflow {data.workflow_id}.")
    _drive_workflow_interactively(console, client, config, data.workflow_id)


def _drive_workflow_interactively(
    console: Console, client: PlatformClient, config: CLIConfig, workflow_id: str
) -> None:
    """Keep `workflow_id` moving forward in this process until it reaches a
    halt status (COMPLETED/FAILED/CANCELLED/REVISION_REQUIRED), prompting
    inline for any clarification/approval the server surfaces along the
    way -- see docs/architecture/v1_architecture.md §12.1. Each server call
    already auto-advances through every already-completed stage on its own,
    so this loop only needs to react to whatever the server stops it at.
    """
    interactive = _is_interactive_session()
    while True:
        try:
            status = client.get_status(workflow_id)
        except ConnectionUnavailable as exc:
            _fail_connection(console, config, exc)
            return
        except APIError as exc:
            _fail_api(console, exc)
            return

        formatters.render_pipeline(console, status)

        if status.status in formatters.HALT_STATUSES:
            return

        pending = status.pending_action
        if pending is None:
            formatters.render_warning(
                console,
                "Workflow is running with no pending action reported; stopping here. "
                "Check again shortly with [bold]ai-sdlc status[/bold].",
            )
            return

        if not interactive:
            console.print(
                "Non-interactive session: not prompting. Use "
                "[bold]ai-sdlc answer[/bold]/[bold]ai-sdlc approve[/bold]/[bold]ai-sdlc reject[/bold] "
                "to continue this workflow."
            )
            return

        try:
            if pending.action_type == "CLARIFICATION" and pending.interaction_id:
                _prompt_and_submit_clarification(console, client, config, workflow_id, pending.interaction_id)
            elif pending.action_type == "APPROVAL" and pending.interaction_id:
                _prompt_and_submit_approval(console, client, config, workflow_id, pending.interaction_id)
            else:
                formatters.render_warning(
                    console,
                    f"Unrecognized pending action ({pending.action_type}); stopping. Use `ai-sdlc status` to inspect.",
                )
                return
        except (KeyboardInterrupt, EOFError):
            console.print()
            formatters.render_warning(console, "Interrupted -- the workflow is left paused at its current stage.")
            console.print(
                "Resume later with [bold]ai-sdlc answer[/bold]/[bold]ai-sdlc approve[/bold]/"
                "[bold]ai-sdlc reject[/bold], or check with [bold]ai-sdlc status[/bold]."
            )
            return
        except ConnectionUnavailable as exc:
            _fail_connection(console, config, exc)
            return
        except APIError as exc:
            _fail_api(console, exc)
            return


def _prompt_and_submit_clarification(
    console: Console, client: PlatformClient, config: CLIConfig, workflow_id: str, question_id: str
) -> None:
    response_text = console.input("[bold]Your answer:[/bold] ").strip()
    while not response_text:
        response_text = console.input("[bold]Your answer (required):[/bold] ").strip()
    data = client.submit_clarification(workflow_id, config.initiator_id, question_id, response_text)
    formatters.render_success(console, data.message)


def _prompt_and_submit_approval(
    console: Console, client: PlatformClient, config: CLIConfig, workflow_id: str, approval_id: str
) -> None:
    decision = console.input("[bold]Approve this artifact? (y/n):[/bold] ").strip().lower()
    while decision not in ("y", "yes", "n", "no"):
        decision = console.input("[bold]Please enter y or n:[/bold] ").strip().lower()
    approved = decision in ("y", "yes")

    feedback: Optional[str] = None
    if not approved:
        feedback = console.input("[bold]Reason for rejection:[/bold] ").strip()
        while not feedback:
            feedback = console.input("[bold]Reason for rejection (required):[/bold] ").strip()

    data = client.submit_approval(workflow_id, config.initiator_id, approval_id, approved, feedback)
    formatters.render_success(console, data.message)


def run_status(console: Console, workflow_id_override: Optional[str]) -> None:
    config = _require_config(console)
    workflow_id = _require_workflow_id(console, config, workflow_id_override)
    client = _client_for(config)
    try:
        status = client.get_status(workflow_id)
    except ConnectionUnavailable as exc:
        _fail_connection(console, config, exc)
        return
    except APIError as exc:
        _fail_api(console, exc)
        return
    formatters.render_pipeline(console, status)


def run_answer(console: Console, response_text: str, workflow_id_override: Optional[str]) -> None:
    config = _require_config(console)
    workflow_id = _require_workflow_id(console, config, workflow_id_override)
    client = _client_for(config)
    try:
        status = client.get_status(workflow_id)
    except ConnectionUnavailable as exc:
        _fail_connection(console, config, exc)
        return
    except APIError as exc:
        _fail_api(console, exc)
        return

    pending = status.pending_action
    if pending is None or pending.action_type != "CLARIFICATION" or not pending.interaction_id:
        formatters.render_error(console, "No pending clarification for this workflow.")
        if pending is not None:
            console.print(f"Pending action is {pending.action_type}; use the matching command instead.")
        raise typer.Exit(code=1)

    try:
        data = client.submit_clarification(workflow_id, config.initiator_id, pending.interaction_id, response_text)
    except ConnectionUnavailable as exc:
        _fail_connection(console, config, exc)
        return
    except APIError as exc:
        _fail_api(console, exc)
        return

    formatters.render_success(console, data.message)
    _refresh_and_render(console, client, workflow_id)


def _submit_approval_decision(
    console: Console, approved: bool, feedback: Optional[str], workflow_id_override: Optional[str]
) -> None:
    config = _require_config(console)
    workflow_id = _require_workflow_id(console, config, workflow_id_override)
    client = _client_for(config)
    try:
        status = client.get_status(workflow_id)
    except ConnectionUnavailable as exc:
        _fail_connection(console, config, exc)
        return
    except APIError as exc:
        _fail_api(console, exc)
        return

    pending = status.pending_action
    if pending is None or pending.action_type != "APPROVAL" or not pending.interaction_id:
        formatters.render_error(console, "No pending approval for this workflow.")
        if pending is not None:
            console.print(f"Pending action is {pending.action_type}; use the matching command instead.")
        raise typer.Exit(code=1)

    try:
        data = client.submit_approval(workflow_id, config.initiator_id, pending.interaction_id, approved, feedback)
    except ConnectionUnavailable as exc:
        _fail_connection(console, config, exc)
        return
    except APIError as exc:
        _fail_api(console, exc)
        return

    formatters.render_success(console, data.message)
    _refresh_and_render(console, client, workflow_id)


def run_approve(console: Console, workflow_id_override: Optional[str]) -> None:
    _submit_approval_decision(console, approved=True, feedback=None, workflow_id_override=workflow_id_override)


def run_reject(console: Console, reason: str, workflow_id_override: Optional[str]) -> None:
    _submit_approval_decision(console, approved=False, feedback=reason, workflow_id_override=workflow_id_override)


def run_cancel(console: Console, reason: str, workflow_id_override: Optional[str]) -> None:
    config = _require_config(console)
    workflow_id = _require_workflow_id(console, config, workflow_id_override)
    client = _client_for(config)
    try:
        data = client.cancel_workflow(workflow_id, config.initiator_id, reason)
    except ConnectionUnavailable as exc:
        _fail_connection(console, config, exc)
        return
    except APIError as exc:
        _fail_api(console, exc)
        return
    formatters.render_success(console, f"Workflow {data.workflow_id} cancelled at {data.cancelled_at}.")
