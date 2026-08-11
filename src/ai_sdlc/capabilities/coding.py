"""Coding capability abstraction.

**RECONCILIATION NOTICE:** `src/ai_sdlc/capabilities/coding.py` is owned by
Claude Forge (the Claude Agent SDK provider, Nova's confirmed V1/default
coding provider) -- see `docs/architecture/v1_architecture.md` section 3's
"Coding Capability Adapter" row. This file did not exist yet when Copilot
Forge (the GitHub Copilot provider, running in a separate isolated
worktree in parallel) needed it, so this is Copilot Forge's best-effort
proposal, grounded strictly in section 3/8's conceptual shape -- **not**
a final interface. If Claude Forge's own version of this file differs,
that version should win; the two need to be reconciled (by hand or by a
follow-up PR) before both branches merge. See Copilot Forge's final report
for the specific shape decisions made here and where they might conflict.

This is the boundary the (not yet built) Developer Agent (Forge) calls
through instead of depending on a specific agentic coding tool/SDK.
Concrete providers implement `CodingCapability.execute()`; the Developer
Agent only ever sees this interface. Mirrors `capabilities/reasoning.py`'s
`ReasoningCapability` and `capabilities/design.py`'s `DesignCapability`
seams for the shared failure contract (`ProviderError` /
`MalformedResponseError`) and Pydantic-validated request/response shape,
but differs in one deliberate way that section 8 calls out explicitly:
`CodingCapability` wraps a provider's own *bounded agentic loop*
(read/edit/run-command, repeated until done or a step limit is hit)
rather than a single completion -- so unlike `complete()`/`generate()`,
`execute()` may take meaningfully longer and do real filesystem/process
work under the hood. The interface itself still stays synchronous and
single-call from the Developer Agent's perspective; providers whose
underlying SDK is async/session-based (both Claude Agent SDK and
`github/copilot-sdk` are) are responsible for absorbing that asymmetry
internally (e.g. via `asyncio.run`) and returning one validated result.

Conceptually:

    Developer Agent (Forge, not yet built)
            |
            v
    CodingCapability   (this module)
            |
            v
    CodingProvider Protocol (providers/coding_base.py)
            |
            v
    Configured provider (providers/coding_mock.py for tests/V1 default;
    providers/coding_copilot.py for the GitHub Copilot SDK-backed
    provider; a Claude Agent SDK-backed provider is Claude Forge's
    parallel work)

Request/response shape (Copilot Forge's derived design):
`docs/architecture/v1_architecture.md` section 3's "Coding Capability
Adapter" row gives the conceptual contract -- "task/context payload,
target working-tree path, allowed-tool/command policy" in; "structured
change summary, self-check results, provider response envelope" out --
not a literal schema the way it does for the Developer Agent's own
`DeveloperAgentInput`/`DeveloperAgentOutput` (section 4). `CodingRequest`/
`CodingResult` below are therefore derived schemas, following the same
field-naming/density and `field_validator` convention already established
by `DesignRequest`/`DesignResponse`, deliberately kept provider-agnostic
(nothing Copilot-SDK-specific) so a Claude Agent SDK provider fits the
same shape. In particular:

  - `CodingRequest` is narrower than `DeveloperAgentInput` (section 4):
    the Developer Agent is responsible for assembling requirements +
    architecture + approved UX package + Standards Context into the
    `task_brief`/`standards_context` text fields before calling this
    capability -- `CodingCapability` itself has no opinion on PO/
    Architecture/UX schemas, only on the flattened task text a coding
    tool needs plus where/how it's allowed to act.
  - `tool_policy.allowed_commands` mirrors section 10's Tool Sandboxing
    allow-list (`git`, `mvn`, `gradle`, `npm`, `pytest`, ...) verbatim --
    this is the same allow-list, not a Copilot-specific one.
  - `self_check_commands` being empty is a valid, meaningful input (not
    an oversight): section 20 open question 7 asks what a provider should
    do when a codebase has no configured build/test commands. This
    interface represents "no commands configured" as an empty list rather
    than `None`, so a provider can distinguish "run these" from "there
    was nothing to run" and report that distinction back in
    `SelfCheckResult.skipped_reason` rather than silently reporting
    `False`/`False` (which would look like a failed check, not an absent
    one). See `providers/coding_copilot.py` module docstring for Copilot
    Forge's documented answer to Q7, and `max_steps` below for Q8.
  - `max_steps` is this interface's answer to section 20 open question 8
    (the agentic loop's retry/step budget): a per-request, provider-
    agnostic ceiling the Developer Agent can override per call, with a
    conservative platform default (see field default). Whether this
    should also be configurable per-workspace (like `coding_provider`
    itself, section 12) rather than only per-request is left to whoever
    builds the Developer Agent -- this capability only needs the value to
    exist at call time.
  - `revision_feedback` threads the same "rejection feedback re-enters as
    an additional input" pattern already used by UX's revision loop
    (section 6, "UX Revision & Feedback Loop") and PO's clarification
    loop -- the Developer Agent's own output goes through the identical
    approval gate a second time (section 6, "UX -> Developer Agent
    Handoff Contract", closing paragraph), so this capability needs a
    place to receive that feedback on a re-attempt.
  - `CodingResult.self_check` mirrors `DeveloperAgentOutput.data.self_check`
    (section 4) field-for-field (`build_passed`, `tests_passed`,
    `commands_run`) plus one addition, `skipped_reason`, for the Q7 case
    above -- the Developer Agent's output schema already anticipates this
    capability's self-check result being passed through close to as-is.
  - `CodingResult.files_changed` uses a structured `FileChange` (path +
    change type) rather than the bare path strings shown in
    `DeveloperAgentOutput`'s example JSON (section 4) -- the extra
    `change_type` is additive information a coding-tool provider already
    knows (it made the edit), and the Developer Agent can trivially
    project it back down to bare paths if section 4's literal example
    shape is what ships. Flagged here as a possible reconciliation point.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class ProviderError(Exception):
    """Raised when the underlying coding provider fails to produce a
    result at all (e.g. the agentic tool crashed, could not authenticate,
    or a network/vendor outage occurred before any work was done).
    Callers should generally treat this as a retryable condition."""


class MalformedResponseError(Exception):
    """Raised when the underlying coding provider *did* run, but its
    result could not be parsed/validated into a `CodingResult` -- e.g. the
    provider's own session ended without a coherent final state."""


def _nonempty_str(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise ValueError("must not be empty")
    return value


class ToolPolicy(BaseModel):
    """The allow-list + isolated-worktree enforcement model from section
    10's "Tool Sandboxing" bullet, expressed as capability input rather
    than provider-internal configuration. A provider must deny, not
    prompt for, anything outside `allowed_commands` -- see section 10:
    "invoked in an unattended permission mode that denies, rather than
    prompts for, any tool/command outside the allow-list.\""""

    allowed_commands: List[str] = Field(
        default_factory=lambda: ["git", "npm", "pytest", "mvn", "gradle"]
    )
    #: Explicitly denied even if a provider's own defaults would allow it
    #: (e.g. `sudo`, raw network tools) -- belt-and-suspenders on top of
    #: `allowed_commands` already being a strict allow-list, matching
    #: section 10's explicit callout of destructive commands.
    denied_commands: List[str] = Field(
        default_factory=lambda: ["sudo", "rm"]
    )

    @field_validator("allowed_commands")
    @classmethod
    def _allowed_nonempty(cls, value: List[str]) -> List[str]:
        if not value:
            raise ValueError("allowed_commands must contain at least one command")
        return value


class CodingRequest(BaseModel):
    """Input payload to `CodingCapability.execute()`: the task/context
    payload, target working-tree path, and allowed-tool/command policy
    described in `docs/architecture/v1_architecture.md` section 3."""

    #: Short, human-scannable description of the change (mirrors
    #: `DeveloperAgentOutput.data.branch_name`'s intent -- providers may
    #: use this to help name the branch).
    task_summary: str
    #: The full assembled task text: requirements + architecture context
    #: + approved UX package + revision feedback, already flattened to
    #: text by the Developer Agent before this call. This capability does
    #: not parse structured requirements/architecture schemas itself.
    task_brief: str
    #: Curated org/project convention text from the Standards Context
    #: Layer (section 9.1), injected directly -- not retrieved -- the same
    #: way it is injected into every other tier's prompt.
    standards_context: str = ""
    #: Absolute path to the **target repository root** (matching
    #: `DeveloperAgentInput.target_repository.workspace_path` in section
    #: 4's literal example) -- not a pre-isolated working tree. Per
    #: section 4 point 1 and section 7, creating the disposable, isolated
    #: Git worktree/branch off this root is the *provider's* own
    #: responsibility; the provider must never run edits/commands
    #: directly against this path itself, only against the worktree it
    #: creates from it. See `providers/coding_copilot.py`'s module
    #: docstring for the reasoning and the flag that this is a
    #: reconciliation point if Claude Forge's version assumes the
    #: opposite (caller-created, already-isolated `workspace_path`).
    workspace_path: str
    base_branch: str = "main"
    tool_policy: ToolPolicy = Field(default_factory=ToolPolicy)
    #: The codebase's own build/test commands to run as a self-check
    #: after the agentic loop finishes (section 4's "Execution Model").
    #: An empty list is a valid, meaningful input -- see this module's
    #: docstring and section 20 open question 7.
    self_check_commands: List[str] = Field(default_factory=list)
    #: Bounded step/attempt ceiling for the provider's agentic loop
    #: (section 20 open question 8). 40 is a conservative default chosen
    #: by Copilot Forge, not an architecture-doc-specified number --
    #: revisit once real usage data exists.
    max_steps: int = 40
    #: Present only on a re-attempt after a human rejection, threading the
    #: reviewer's feedback forward the same way UX's revision loop does
    #: (section 6).
    revision_feedback: Optional[str] = None
    #: Opaque, provider-specific hints, mirroring `DesignRequest`'s
    #: `provider_policy` field exactly. The Developer Agent never inspects
    #: vendor-specific keys itself.
    provider_policy: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("task_summary", "task_brief", "workspace_path", "base_branch")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        return _nonempty_str(value)

    @field_validator("max_steps")
    @classmethod
    def _max_steps_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("max_steps must be at least 1")
        return value


class FileChange(BaseModel):
    """One file touched by the provider's agentic loop."""

    path: str
    change_type: str  # one of: "ADDED", "MODIFIED", "DELETED"

    @field_validator("path")
    @classmethod
    def _nonempty_path(cls, value: str) -> str:
        return _nonempty_str(value)

    @field_validator("change_type")
    @classmethod
    def _valid_change_type(cls, value: str) -> str:
        allowed = {"ADDED", "MODIFIED", "DELETED"}
        if value not in allowed:
            raise ValueError(f"change_type must be one of {sorted(allowed)}, got {value!r}")
        return value


class SelfCheckResult(BaseModel):
    """Mirrors `DeveloperAgentOutput.data.self_check` (section 4) field-
    for-field, plus `skipped_reason` for the section 20 open question 7
    case -- see this module's docstring."""

    build_passed: Optional[bool] = None
    tests_passed: Optional[bool] = None
    commands_run: List[str] = Field(default_factory=list)
    #: Populated (only) when `commands_run` is empty because no self-check
    #: commands were configured -- see `CodingRequest.self_check_commands`
    #: and this module's docstring. `None` when a self-check actually ran.
    skipped_reason: Optional[str] = None


class CodingResult(BaseModel):
    """Provider response envelope returned by `CodingCapability.execute()`:
    structured change summary + self-check results + provider response
    envelope, per `docs/architecture/v1_architecture.md` section 3."""

    branch_name: str
    files_changed: List[FileChange]
    self_check: SelfCheckResult
    #: Free-text summary of what the provider actually did -- the
    #: "structured change summary" section 3 calls for, at the level of
    #: detail a human reviewer would want before approving.
    summary: str
    #: How many loop steps the provider actually used, out of
    #: `CodingRequest.max_steps` -- lets the Developer Agent detect
    #: "finished early" vs. "hit the ceiling" (section 20 open question 8).
    steps_used: int
    provider_name: str
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("branch_name", "summary", "provider_name")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        return _nonempty_str(value)

    @field_validator("steps_used")
    @classmethod
    def _steps_used_nonnegative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("steps_used must not be negative")
        return value


class CodingCapability(ABC):
    """Abstract coding capability.

    The Developer Agent calls `execute(request)` and receives back a
    validated `CodingResult` describing the isolated branch/commit(s) the
    provider produced, the files it changed, and the self-check outcome.
    Implementations are responsible for whatever agentic-loop driving,
    permission resolution, and validation is needed to satisfy
    `CodingResult`, and must raise `ProviderError` or
    `MalformedResponseError` (never an arbitrary/unrelated exception) on
    failure -- mirroring `ReasoningCapability`/`DesignCapability`'s
    failure contract exactly, so the Developer Agent can handle all three
    capabilities uniformly.
    """

    @abstractmethod
    def execute(self, request: CodingRequest) -> CodingResult:
        """Run the provider's bounded agentic coding loop against
        `request.workspace_path` and return a validated `CodingResult`.

        Raises:
            ProviderError: the provider could not produce a result at all.
            MalformedResponseError: the provider ran, but its result does
                not satisfy `CodingResult`.
        """
        raise NotImplementedError()
