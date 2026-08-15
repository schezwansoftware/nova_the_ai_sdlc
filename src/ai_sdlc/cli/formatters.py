"""Rich rendering for CLI output. No orchestration logic lives here -- only
presentation of whatever `WorkflowStatusData` the server already returned.

Rendering is split into two layers: `*_renderable()` functions build and
return a Rich renderable (Table/Panel/Group) without printing anything, and
`render_*()` functions print one to a `Console` -- the plain-CLI path this
module has always supported. The TUI (`cli/tui.py`) is a second consumer of
the same `*_renderable()` functions, writing them into its own transcript
widget instead of a `Console`, so pipeline/clarification/approval rendering
has one source of truth rather than two copies that could drift."""
from __future__ import annotations

from typing import Optional

from rich.console import Console, Group, RenderableType
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ai_sdlc.cli.schemas import PendingAction, WorkflowStatusData
from ai_sdlc.cli.version import CLI_VERSION

# "NOVA" wordmark (figlet "doom" font) -- Nova is this platform's product
# name; `ai-sdlc` is just the command you type to reach it. Each line is
# rendered in its own shade below for a top-to-bottom gradient. Generated
# via `pyfiglet.figlet_format("NOVA", font="doom")` -- edit only by
# regenerating from that, not by hand (glyph alignment is spacing-exact).
_BANNER_ART = (
    " _   _ _____  _   _  ___  ",
    "| \\ | |  _  || | | |/ _ \\ ",
    "|  \\| | | | || | | / /_\\ \\",
    "| . ` | | | || | | |  _  |",
    "| |\\  \\ \\_/ /\\ \\_/ / | | |",
    "\\_| \\_/\\___/  \\___/\\_| |_/",
)
_BANNER_GRADIENT = ("bright_cyan", "bright_cyan", "cyan", "cyan", "blue", "bright_blue")

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


def banner_renderable() -> Group:
    lines: list[RenderableType] = [Text(line, style=f"bold {color}") for line, color in zip(_BANNER_ART, _BANNER_GRADIENT)]
    lines.append(Text(f"ai-sdlc  v{CLI_VERSION}  --  AI-powered SDLC automation platform", style="dim"))
    lines.append("")
    lines.append(
        Text(
            "Turns a raw requirement into a requirements spec, architecture, and UX "
            "design through a Product Owner -> Architecture -> UX Design agent "
            "pipeline, pausing to ask you for clarification or approval as needed.",
            style="white",
        )
    )
    lines.append("")
    lines.append(
        Text.assemble(
            ("Commands: ", "bold"),
            ("init, start, status, answer, approve, reject, cancel", "cyan"),
        )
    )
    lines.append(
        Text.assemble(
            "Run ",
            ("ai-sdlc --help", "bold cyan"),
            " or ",
            ("ai-sdlc <command> --help", "bold cyan"),
            " for details.",
        )
    )
    return Group(*lines)


def render_banner(console: Console) -> None:
    """Shown once, when `start` is about to ask the user for their
    requirement interactively -- gives the session a clear identity (a
    wordmark, version, description) before it starts prompting."""
    console.print()
    console.print(banner_renderable())
    console.print()


def pipeline_table_renderable(status: WorkflowStatusData) -> Table:
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
        skipped = artifact_key is not None and status.artifacts.get(artifact_key) == "skipped"
        if completed:
            marker, style = "done", "green"
        elif skipped:
            marker, style = "skipped (no UI)", "dim"
        elif phase == status.current_phase and is_terminal:
            marker, style = ("done" if status.status == "COMPLETED" else status.status.lower()), (
                "green" if status.status == "COMPLETED" else "red"
            )
        elif phase == status.current_phase:
            marker, style = status.status.lower(), "yellow"
        else:
            marker, style = "pending", "dim"
        table.add_row(phase, f"[{style}]{escape(marker)}[/{style}]")

    return table


def _terminal_panel_renderable(status: WorkflowStatusData) -> Optional[Panel]:
    if status.status == "COMPLETED":
        return Panel("Workflow completed.", style="green")
    if status.status in ("FAILED", "CANCELLED"):
        return Panel(f"Workflow {status.status.lower()}.", style="red")
    if status.status == "REVISION_REQUIRED":
        return Panel(
            "Workflow needs revision based on reviewer feedback. "
            "No further pipeline stages will run automatically.",
            style="yellow",
        )
    return None


def pending_action_renderable(pending: PendingAction, *, hint: Optional[str] = None) -> Panel:
    """`hint` lets a caller substitute how to respond -- the plain CLI's
    default is the scriptable `ai-sdlc answer`/`approve`/`reject` commands,
    but the TUI (`cli/tui.py`) responds to the same pending action through
    its own input box, so it passes a different hint rather than telling a
    user mid-TUI-session to go run a separate shell command."""
    prompt = escape(pending.prompt_message)
    if pending.action_type == "CLARIFICATION":
        respond_hint = hint if hint is not None else 'Respond with:\n  ai-sdlc answer "<your response>"'
        return Panel(
            f"{prompt}\n\n{respond_hint}",
            title="Clarification requested",
            style="yellow",
        )
    if pending.action_type == "APPROVAL":
        detail = prompt
        if pending.payload_artifact_path:
            detail += f"\nArtifact: {escape(pending.payload_artifact_path)}"
        respond_hint = (
            hint if hint is not None else 'Respond with:\n  ai-sdlc approve\n  ai-sdlc reject --reason "<reason>"'
        )
        return Panel(
            f"{detail}\n\n{respond_hint}",
            title="Approval requested",
            style="yellow",
        )
    return Panel(prompt, title=escape(pending.action_type), style="yellow")


def workflow_status_renderable(status: WorkflowStatusData, *, pending_hint: Optional[str] = None) -> Group:
    """Everything `render_pipeline` prints for one status snapshot (pipeline
    table, updated_at line, and whichever terminal/pending panel applies),
    bundled as a single renderable so a caller -- plain CLI or the TUI --
    only has to write/print one thing. `pending_hint` is forwarded to
    `pending_action_renderable` (see its docstring)."""
    parts: list[RenderableType] = [
        pipeline_table_renderable(status),
        f"[dim]updated_at: {escape(status.updated_at)}[/dim]",
    ]
    terminal_panel = _terminal_panel_renderable(status)
    if terminal_panel is not None:
        parts.append(terminal_panel)
    elif status.pending_action is not None:
        parts.append(pending_action_renderable(status.pending_action, hint=pending_hint))
    return Group(*parts)


def render_pipeline(console: Console, status: WorkflowStatusData) -> None:
    console.print(workflow_status_renderable(status))


def render_pending_action(console: Console, pending: PendingAction) -> None:
    console.print(pending_action_renderable(pending))


def success_markup(message: str) -> str:
    return f"[green]{escape(message)}[/green]"


def warning_markup(message: str) -> str:
    return f"[yellow]{escape(message)}[/yellow]"


def error_markup(message: str) -> str:
    return f"[red]{escape(message)}[/red]"


def render_success(console: Console, message: str) -> None:
    console.print(success_markup(message))


def render_warning(console: Console, message: str) -> None:
    console.print(warning_markup(message))


def render_error(console: Console, message: str) -> None:
    console.print(error_markup(message))
