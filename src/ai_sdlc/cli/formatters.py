"""Rich rendering for CLI output. No orchestration logic lives here -- only
presentation of whatever `WorkflowStatusData` the server already returned."""
from __future__ import annotations

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from ai_sdlc.cli.schemas import PendingAction, WorkflowStatusData

# Real execution order of the current workflow graph (see
# DEFAULT_WORKFLOW_NODES in ai_sdlc.orchestration.langgraph_runner: this is
# read-only reference, never imported). Kept as a CLI-local rendering
# constant, not a copy of orchestration logic -- if the graph grows new
# stages, an unrecognized `current_phase` is still rendered (appended), it
# just won't have a fixed position until this list is updated.
PIPELINE_PHASES = ["REQUIREMENTS", "ARCHITECTURE", "UX_DESIGN"]
TERMINAL_PHASES = {"COMPLETED", "FAILED", "CANCELLED"}
# Statuses that stop `start`'s interactive loop (see handlers.py): the three
# truly terminal ones above, plus REVISION_REQUIRED, which halts automatic
# progress on rejection without being a terminal workflow state itself.
HALT_STATUSES = TERMINAL_PHASES | {"REVISION_REQUIRED"}
_ARTIFACT_KEY_BY_PHASE = {
    "REQUIREMENTS": "requirements",
    "ARCHITECTURE": "architecture",
    "UX_DESIGN": "ux_design",
}


def render_pipeline(console: Console, status: WorkflowStatusData) -> None:
    table = Table(title=f"Workflow {escape(status.workflow_id)}", show_header=True, header_style="bold")
    table.add_column("Stage")
    table.add_column("State")

    phases = list(PIPELINE_PHASES)
    if status.current_phase not in phases and status.current_phase not in TERMINAL_PHASES | {"INIT"}:
        phases.append(status.current_phase)

    is_terminal = status.status in TERMINAL_PHASES
    for phase in phases:
        artifact_key = _ARTIFACT_KEY_BY_PHASE.get(phase)
        completed = artifact_key is not None and status.artifacts.get(artifact_key) == "completed"
        if completed:
            marker, style = "done", "green"
        elif phase == status.current_phase and is_terminal:
            marker, style = ("done" if status.status == "COMPLETED" else status.status.lower()), (
                "green" if status.status == "COMPLETED" else "red"
            )
        elif phase == status.current_phase:
            marker, style = status.status.lower(), "yellow"
        else:
            marker, style = "pending", "dim"
        table.add_row(phase, f"[{style}]{escape(marker)}[/{style}]")

    console.print(table)
    console.print(f"[dim]updated_at: {escape(status.updated_at)}[/dim]")

    if status.status == "COMPLETED":
        console.print(Panel("Workflow completed.", style="green"))
    elif status.status in ("FAILED", "CANCELLED"):
        console.print(Panel(f"Workflow {status.status.lower()}.", style="red"))
    elif status.status == "REVISION_REQUIRED":
        console.print(
            Panel(
                "Workflow needs revision based on reviewer feedback. "
                "No further pipeline stages will run automatically.",
                style="yellow",
            )
        )
    elif status.pending_action is not None:
        render_pending_action(console, status.pending_action)


def render_pending_action(console: Console, pending: PendingAction) -> None:
    prompt = escape(pending.prompt_message)
    if pending.action_type == "CLARIFICATION":
        console.print(
            Panel(
                f'{prompt}\n\nRespond with:\n  ai-sdlc answer "<your response>"',
                title="Clarification requested",
                style="yellow",
            )
        )
    elif pending.action_type == "APPROVAL":
        detail = prompt
        if pending.payload_artifact_path:
            detail += f"\nArtifact: {escape(pending.payload_artifact_path)}"
        console.print(
            Panel(
                f'{detail}\n\nRespond with:\n  ai-sdlc approve\n  ai-sdlc reject --reason "<reason>"',
                title="Approval requested",
                style="yellow",
            )
        )
    else:
        console.print(Panel(prompt, title=escape(pending.action_type), style="yellow"))


def render_success(console: Console, message: str) -> None:
    console.print(f"[green]{escape(message)}[/green]")


def render_warning(console: Console, message: str) -> None:
    console.print(f"[yellow]{escape(message)}[/yellow]")


def render_error(console: Console, message: str) -> None:
    console.print(f"[red]{escape(message)}[/red]")
