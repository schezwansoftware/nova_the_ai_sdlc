"""Full-screen interactive session for `ai-sdlc start` (see
docs/architecture/v1_architecture.md §12.1). A `textual` app: a scrolling
transcript filling the screen, a single input box docked at the bottom for
both the initial requirement and every clarification/approval reply after
it, and a one-line status bar.

Drives the exact same server contract `handlers._drive_workflow_interactively`
does (start_workflow -> poll get_status -> react to pending_action ->
submit_clarification/submit_approval -> poll again) -- this module only
changes how that loop is presented, not what it calls. Rendering reuses
`formatters.py`'s renderable-building functions so the plain CLI and this
TUI never have two copies of what a pipeline/clarification/approval panel
looks like.

Every `PlatformClient` call is blocking HTTP (see `client.py`), so each one
runs on a Textual worker thread (`@work(thread=True)`) and hands its result
back to the main thread via `call_from_thread` -- the event loop, and the
elapsed-time "Thinking..." indicator, keep running while it's in flight.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional, Tuple

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.timer import Timer
from textual.widgets import Input, RichLog, Static

from ai_sdlc.cli import formatters
from ai_sdlc.cli.client import APIError, ConnectionUnavailable, PlatformClient
from ai_sdlc.cli.config import CLIConfig, save_config
from ai_sdlc.cli.schemas import WorkflowStatusData

# Mirrors handlers.py's own `_MIN_REQUIREMENT_LENGTH` (itself mirroring
# StartWorkflowRequest's `Field(..., min_length=10)`); duplicated rather
# than imported to avoid a handlers.py <-> tui.py circular import (handlers
# lazily imports NovaApp to launch it).
_MIN_REQUIREMENT_LENGTH = 10

_CLARIFICATION_HINT = "Type your answer below and press Enter."
_APPROVAL_HINT = "Type y to approve or n to reject, then press Enter."

# (kind, reference_id) describing what the next Input submission means:
#   ("requirement", "")               -- initial requirement text
#   ("clarification", question_id)    -- free-text answer
#   ("approval_decision", approval_id)-- "y"/"n"
#   ("approval_reason", approval_id)  -- rejection reason, after "n"
_AwaitState = Tuple[str, str]


class NovaApp(App[None]):
    CSS = """
    Screen {
        layout: vertical;
        background: $surface;
    }
    #transcript {
        height: 1fr;
        border: none;
        padding: 0 1;
    }
    #thinking {
        height: 1;
        padding: 0 1;
        display: none;
        color: $text-muted;
    }
    #thinking.visible {
        display: block;
    }
    #input {
        height: 3;
        margin: 0 1;
        border: round $accent;
    }
    #status_bar {
        height: 1;
        background: $panel;
        color: $text-muted;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", priority=True),
    ]

    def __init__(self, config: CLIConfig, client: PlatformClient, initial_prompt: Optional[str]):
        super().__init__()
        self.config = config
        self.client = client
        self.initial_prompt = initial_prompt
        self.workflow_id: Optional[str] = None
        #: Set on quit if a workflow was started but never reached a halt
        #: status -- the caller (handlers.run_start) prints the usual
        #: resume hint after this app exits, mirroring
        #: `_drive_workflow_interactively`'s Ctrl-C handling.
        self.left_workflow_paused = False

        self._awaiting: Optional[_AwaitState] = None
        self._reached_halt = False
        self._thinking_start = 0.0
        self._thinking_timer: Optional[Timer] = None

    # -- layout ------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield RichLog(id="transcript", markup=True, wrap=True, auto_scroll=True)
        yield Static("", id="thinking")
        yield Input(placeholder="Define your requirement, or paste a path to a requirements.txt file...", id="input")
        yield Static("", id="status_bar")

    def on_mount(self) -> None:
        self._refresh_status_bar()
        log = self.query_one("#transcript", RichLog)
        log.write(formatters.banner_renderable())
        if self.initial_prompt:
            self._begin_start_workflow(self.initial_prompt)
        else:
            log.write("Define your requirement, or paste a path to a requirements.txt file, then press Enter.")
            self._awaiting = ("requirement", "")
        self.query_one("#input", Input).focus()

    def action_quit(self) -> None:
        self.left_workflow_paused = bool(self.workflow_id) and not self._reached_halt
        self.exit()

    # -- input handling ------------------------------------------------

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        input_widget = self.query_one("#input", Input)
        input_widget.value = ""
        if self._awaiting is None:
            return

        kind, ref_id = self._awaiting
        log = self.query_one("#transcript", RichLog)

        if kind == "requirement":
            if not value:
                log.write("[dim]A requirement is needed to continue.[/dim]")
                return
            candidate_path = Path(value).expanduser()
            text = value
            if candidate_path.is_file():
                text = candidate_path.read_text(encoding="utf-8").strip()
                log.write(f"[dim]Read requirement from {candidate_path}.[/dim]")
            if len(text) < _MIN_REQUIREMENT_LENGTH:
                log.write(
                    f"[yellow]That's {len(text)} character(s); the requirement needs to be at least "
                    f"{_MIN_REQUIREMENT_LENGTH}. Please add more detail.[/yellow]"
                )
                return
            self._awaiting = None
            self._begin_start_workflow(text)
            return

        if kind == "clarification":
            if not value:
                log.write("[dim]An answer is required.[/dim]")
                return
            self._awaiting = None
            self._begin_submit_clarification(ref_id, value)
            return

        if kind == "approval_decision":
            decision = value.lower()
            if decision in ("y", "yes"):
                self._awaiting = None
                self._begin_submit_approval(ref_id, True, None)
                return
            if decision in ("n", "no"):
                self._awaiting = ("approval_reason", ref_id)
                log.write("[bold]Reason for rejection:[/bold]")
                return
            log.write("[yellow]Please enter y or n.[/yellow]")
            return

        if kind == "approval_reason":
            if not value:
                log.write("[dim]A reason is required.[/dim]")
                return
            self._awaiting = None
            self._begin_submit_approval(ref_id, False, value)
            return

    # -- busy / thinking indicator --------------------------------------

    def _set_busy(self, active: bool) -> None:
        input_widget = self.query_one("#input", Input)
        input_widget.disabled = active
        if not active:
            input_widget.focus()

    def _set_thinking(self, active: bool) -> None:
        thinking = self.query_one("#thinking", Static)
        if active:
            self._set_busy(True)
            self._thinking_start = time.monotonic()
            thinking.add_class("visible")
            self._thinking_timer = self.set_interval(0.25, self._update_thinking)
            self._update_thinking()
        else:
            if self._thinking_timer is not None:
                self._thinking_timer.stop()
                self._thinking_timer = None
            thinking.remove_class("visible")
            self._set_busy(False)

    def _update_thinking(self) -> None:
        thinking = self.query_one("#thinking", Static)
        elapsed = time.monotonic() - self._thinking_start
        framework = self.config.agent_framework or "mock"
        thinking.update(f"[dim]Thinking (using {framework}) -- {elapsed:.0f}s...[/dim]")

    def _refresh_status_bar(self, status: Optional[WorkflowStatusData] = None) -> None:
        bar = self.query_one("#status_bar", Static)
        bits = [f"workspace: {self.config.workspace}"]
        if self.workflow_id:
            bits.append(f"workflow: {self.workflow_id}")
        if status is not None:
            bits.append(f"phase: {status.current_phase}")
            bits.append(f"status: {status.status}")
        if self.config.agent_framework:
            bits.append(f"agent: {self.config.agent_framework}")
        bar.update("  |  ".join(bits))

    # -- server calls (each runs on a worker thread) ---------------------

    def _on_call_error(self, exc: Exception) -> None:
        self._set_thinking(False)
        log = self.query_one("#transcript", RichLog)
        if isinstance(exc, ConnectionUnavailable):
            log.write(formatters.error_markup(str(exc)))
        else:
            assert isinstance(exc, APIError)
            log.write(formatters.error_markup(f"{exc.code}: {exc.message}"))
        log.write("[dim]Press ctrl+c to exit, or fix the issue and retry outside with ai-sdlc status.[/dim]")

    def _begin_start_workflow(self, prompt: str) -> None:
        self._set_thinking(True)
        self._run_start_workflow(prompt)

    @work(thread=True, exclusive=True)
    def _run_start_workflow(self, prompt: str) -> None:
        try:
            data = self.client.start_workflow(self.config.initiator_id, prompt, {})
        except (ConnectionUnavailable, APIError) as exc:
            self.call_from_thread(self._on_call_error, exc)
            return
        self.call_from_thread(self._on_start_workflow_success, data.workflow_id)

    def _on_start_workflow_success(self, workflow_id: str) -> None:
        self._set_thinking(False)
        self.workflow_id = workflow_id
        self.config.current_workflow_id = workflow_id
        save_config(self.config)
        log = self.query_one("#transcript", RichLog)
        log.write(formatters.success_markup(f"Started workflow {workflow_id}."))
        self._refresh_status_bar()
        self._begin_poll_status()

    def _begin_poll_status(self) -> None:
        self._set_busy(True)
        self._run_get_status()

    @work(thread=True, exclusive=True)
    def _run_get_status(self) -> None:
        assert self.workflow_id is not None
        try:
            status = self.client.get_status(self.workflow_id)
        except (ConnectionUnavailable, APIError) as exc:
            self.call_from_thread(self._on_call_error, exc)
            return
        self.call_from_thread(self._on_status, status)

    def _on_status(self, status: WorkflowStatusData) -> None:
        self._set_busy(False)
        log = self.query_one("#transcript", RichLog)
        hint = _CLARIFICATION_HINT
        if status.pending_action is not None and status.pending_action.action_type == "APPROVAL":
            hint = _APPROVAL_HINT
        log.write(formatters.workflow_status_renderable(status, pending_hint=hint))
        self._refresh_status_bar(status)

        if status.status in formatters.HALT_STATUSES:
            self._reached_halt = True
            log.write("[dim]Workflow finished. Press ctrl+c (or ctrl+q) to exit.[/dim]")
            return

        pending = status.pending_action
        if pending is None:
            log.write(
                formatters.warning_markup(
                    "Workflow is running with no pending action reported. Check again shortly "
                    "with `ai-sdlc status`, or press ctrl+c to exit."
                )
            )
            return

        if pending.action_type == "CLARIFICATION" and pending.interaction_id:
            self._awaiting = ("clarification", pending.interaction_id)
        elif pending.action_type == "APPROVAL" and pending.interaction_id:
            self._awaiting = ("approval_decision", pending.interaction_id)
        else:
            log.write(
                formatters.warning_markup(
                    f"Unrecognized pending action ({pending.action_type}); stopping. Use `ai-sdlc status` to inspect."
                )
            )
            return
        self.query_one("#input", Input).focus()

    def _begin_submit_clarification(self, question_id: str, answer: str) -> None:
        self._set_thinking(True)
        self._run_submit_clarification(question_id, answer)

    @work(thread=True, exclusive=True)
    def _run_submit_clarification(self, question_id: str, answer: str) -> None:
        assert self.workflow_id is not None
        try:
            data = self.client.submit_clarification(self.workflow_id, self.config.initiator_id, question_id, answer)
        except (ConnectionUnavailable, APIError) as exc:
            self.call_from_thread(self._on_call_error, exc)
            return
        self.call_from_thread(self._on_submit_success, data.message)

    def _begin_submit_approval(self, approval_id: str, approved: bool, feedback: Optional[str]) -> None:
        self._set_thinking(True)
        self._run_submit_approval(approval_id, approved, feedback)

    @work(thread=True, exclusive=True)
    def _run_submit_approval(self, approval_id: str, approved: bool, feedback: Optional[str]) -> None:
        assert self.workflow_id is not None
        try:
            data = self.client.submit_approval(
                self.workflow_id, self.config.initiator_id, approval_id, approved, feedback
            )
        except (ConnectionUnavailable, APIError) as exc:
            self.call_from_thread(self._on_call_error, exc)
            return
        self.call_from_thread(self._on_submit_success, data.message)

    def _on_submit_success(self, message: str) -> None:
        self._set_thinking(False)
        log = self.query_one("#transcript", RichLog)
        log.write(formatters.success_markup(message))
        self._begin_poll_status()
