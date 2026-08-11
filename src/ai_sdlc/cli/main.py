"""Typer entry point for the `ai-sdlc` CLI. Each command is a thin wrapper
around a `handlers.run_*` function -- no HTTP calls or business logic here."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from ai_sdlc.cli import handlers
from ai_sdlc.cli.version import CLI_VERSION

APP_DESCRIPTION = (
    "ai-sdlc: an interactive CLI for the Nova AI SDLC platform.\n\n"
    "`start` drives a raw requirement through the Requirements -> Architecture -> "
    "UX Design agent pipeline in one continuous session, prompting inline for any "
    "clarification or approval it needs along the way. `status`/`answer`/`approve`/"
    "`reject`/`cancel` remain available as one-shot commands for scripting or for "
    "resuming a session you left mid-loop."
)

app = typer.Typer(add_completion=False, help=APP_DESCRIPTION)
console = Console()

_WORKFLOW_ID_OPTION = typer.Option(
    None, "--workflow-id", help="Target a specific workflow instead of the most recently started one."
)


def _version_callback(show_version: bool) -> None:
    if show_version:
        console.print(f"ai-sdlc [bold]{CLI_VERSION}[/bold]")
        raise typer.Exit()


@app.callback()
def _main(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the ai-sdlc CLI version and exit.",
    ),
) -> None:
    return


@app.command()
def init(
    workspace: Path = typer.Option(
        Path.cwd(), "--workspace", help="Target repository the Core Platform API will operate on."
    ),
    host: str = typer.Option("127.0.0.1", "--host", help="Core Platform API host."),
    port: int = typer.Option(8000, "--port", help="Core Platform API port."),
    initiator_id: Optional[str] = typer.Option(
        None, "--initiator-id", help="Identity sent with every request. Defaults to the local username."
    ),
    start_server: bool = typer.Option(
        False,
        "--start-server/--no-start-server",
        help="Start the Core Platform API as a background process if one isn't already reachable.",
    ),
    agent_framework: Optional[str] = typer.Option(
        None,
        "--agent-framework",
        help=(
            "AI agent framework to use for the Coding/Retrieval capabilities (claude or copilot). "
            "A one-time choice: once stored, later `init` runs reuse it unless this flag overrides it. "
            "Prompted for interactively if omitted and nothing is stored yet."
        ),
    ),
) -> None:
    """Write local CLI config and scaffold agent registry metadata for WORKSPACE."""
    handlers.run_init(console, workspace, host, port, initiator_id, start_server, agent_framework)


@app.command()
def start(
    prompt: Optional[str] = typer.Option(
        None,
        "--prompt",
        help=(
            "Raw requirement text (minimum 10 characters). If omitted, you'll be "
            "prompted for it interactively."
        ),
    ),
) -> None:
    """Start a new workflow and drive it to completion interactively.

    Prompts for the requirement if --prompt isn't given (as text, or as a
    path to a file containing it), then loops -- auto-continuing through
    completed stages and prompting inline for any clarification/approval --
    until the workflow reaches a terminal state.
    """
    handlers.run_start(console, prompt)


@app.command()
def status(workflow_id: Optional[str] = _WORKFLOW_ID_OPTION) -> None:
    """Show the current workflow's pipeline/phase status."""
    handlers.run_status(console, workflow_id)


@app.command()
def answer(
    response: str = typer.Argument(..., help="Answer to the pending clarification question."),
    workflow_id: Optional[str] = _WORKFLOW_ID_OPTION,
) -> None:
    """Submit an answer to a pending clarification."""
    handlers.run_answer(console, response, workflow_id)


@app.command()
def approve(workflow_id: Optional[str] = _WORKFLOW_ID_OPTION) -> None:
    """Approve the artifact pending approval for the current workflow."""
    handlers.run_approve(console, workflow_id)


@app.command()
def reject(
    reason: str = typer.Option(..., "--reason", help="Feedback explaining why the artifact was rejected."),
    workflow_id: Optional[str] = _WORKFLOW_ID_OPTION,
) -> None:
    """Reject the artifact pending approval for the current workflow."""
    handlers.run_reject(console, reason, workflow_id)


@app.command()
def cancel(
    reason: str = typer.Option("Cancelled via ai-sdlc CLI.", "--reason", help="Reason recorded for the cancellation."),
    workflow_id: Optional[str] = _WORKFLOW_ID_OPTION,
) -> None:
    """Cancel the current workflow."""
    handlers.run_cancel(console, reason, workflow_id)


if __name__ == "__main__":
    app()
