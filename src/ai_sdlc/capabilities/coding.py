"""Coding capability abstraction.

This is the boundary the (not-yet-built) Developer Agent (Forge) calls
through instead of depending on a specific agentic-coding-tool SDK.
Concrete providers implement `CodingCapability.execute()`; the Developer
Agent only ever sees this interface. Mirrors `capabilities/reasoning.py`'s
`ReasoningCapability` and `capabilities/design.py`'s `DesignCapability`
seams in spirit -- same `ABC` + `ProviderError`/`MalformedResponseError`
failure contract, same strict-Pydantic-request/response discipline -- but
diverges from both in one deliberate way, per
`docs/architecture/v1_architecture.md` section 8: writing code into an
existing repository is inherently iterative (read files, decide what to
change, edit, run commands, react, repeat), so `CodingCapability` wraps a
provider's own *bounded agentic loop* rather than a single completion.
The interface itself stays synchronous and single-call from the caller's
perspective regardless -- `execute(request) -> CodingResult`, never an
async generator or a session object the Developer Agent has to drive. The
asynchronous, session-based reality of whatever SDK actually backs a given
provider is that provider's problem to absorb, not something that leaks
into this module.

Conceptually:

    Developer Agent (Forge, not yet built)
            |
            v
    CodingCapability   (this module)
            |
            v
    Configured provider (`providers/coding_mock.py` for V1/tests; the real
    Claude Agent SDK provider -- see `providers/claude_sdk.py` -- for the
    default V1 runtime provider; a second provider targeting
    `github/copilot-sdk` is built separately behind this same seam)

Request/response shape (Forge's derived design):
`docs/architecture/v1_architecture.md` section 3's "Coding Capability
Adapter" row and section 4's `DeveloperAgentInput`/`DeveloperAgentOutput`
give the conceptual contract -- task/context payload, target working-tree
path, and allowed-tool/command policy in; structured change summary,
self-check results, and provider response envelope out -- but `Developer
AgentInput`/`Output` are the *agent's* contract with its caller, not a
literal schema for this capability. `CodingRequest`/`CodingResult` below
are Forge's derived schemas that carry the same substance in a
provider-agnostic shape, following the same field-naming/density and
`field_validator` convention `DesignRequest`/`DesignResponse` already
established. In particular:

  - `CodingRequest.working_tree_path` is an *already-isolated* Git
    worktree the caller (the Developer Agent) created off the target
    repository before calling this capability -- matching section 3's
    "target working-tree path" input and section 4's "isolated working
    tree... never the initiator's live checkout". This capability and its
    providers never create that isolation themselves and never touch
    anything outside it; they only ever read `base_branch` to compute a
    diff, never check it out or mutate it.
  - `CodingRequest.allowed_tools` / `allowed_commands` are the request's
    own allow-list policy (section 10's Tool Sandboxing), not a hardcoded
    default here -- different target repositories need different
    allow-lists, so the caller supplies one per call.
  - `CodingRequest.build_commands` / `test_commands` being empty is a
    real, expected state (a target repo with no configured build/test
    tooling), not an error. Section 20 Open Question 7 asks what a
    provider should do in that case; Forge's documented answer (see
    `SelfCheckResult.skipped`) is: skip self-checking entirely and say so
    explicitly in the result, rather than silently reporting a false
    "passed" or blocking the whole capability on a live clarification
    round-trip that doesn't exist for this capability (section 20 Open
    Question 2 -- there is no confirmed mid-session pause/resume feature
    in the underlying SDKs; providers run to a final structured verdict).
    A future Standards-Layer-declared per-tech-stack default (Craft's
    territory, not built yet) is a reasonable V2 refinement layered on
    top of this default, not a substitute for it.
  - `CodingRequest.max_steps` answers section 20 Open Question 8 (the
    agentic loop's retry/step budget): it is a per-call override of
    `DEFAULT_MAX_STEPS` (see that constant for the exact number and
    rationale), so the budget is configurable per call -- which in
    practice means configurable per workspace, since the Developer Agent
    is expected to read it from workspace config the same way it reads
    `coding_provider` at `ai-sdlc init` (section 12) -- while still having
    a safe platform-wide default when the caller doesn't set one. A
    single `execute()` call is one bounded attempt, not a hidden retry
    loop; if a provider exhausts its budget without finishing, that is a
    terminal result (`TerminationReason.STEP_BUDGET_EXHAUSTED`) for the
    caller to act on (e.g. via the same human revision-loop pattern the
    UX Agent already uses), not something this capability retries on its
    own.
  - `CodingResult.self_check` is populated by the provider itself, inside
    `execute()`, using `CodingRequest.build_commands`/`test_commands` --
    not deferred to the Developer Agent as a separate post-processing
    step. Section 4's prose describes the Developer Agent "running" the
    self-check, but the Developer Agent doesn't exist yet and
    `DeveloperAgentOutput.self_check` needs to come from somewhere; having
    the capability's own normalized envelope already carry it is what
    lets `CodingResult` map onto `DeveloperAgentOutput` directly, per this
    module's stated goal, without a second not-yet-built component
    duplicating the same build/test invocation logic.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

#: Default bounded-agentic-loop step budget (section 20 Open Question 8).
#: A "step" is one provider-defined unit of agentic work -- for the Claude
#: Agent SDK provider this is one turn (one model round-trip, which may
#: itself issue several tool calls); other providers document their own
#: mapping. 40 is chosen as a fixed platform-wide default that comfortably
#: covers a moderate-sized change (read several files, make edits across
#: a handful of them, run build/test commands, react to failures at least
#: once) without letting a provider that can't converge run indefinitely
#: or accumulate runaway cost -- the same "fail cleanly instead of
#: spinning forever" intent as the Orchestrator's existing `max_attempts`
#: ceiling for specialist agents, sized up because a single step here is
#: far cheaper than a full specialist-agent attempt. Callers needing a
#: different ceiling for a given workspace/task set
#: `CodingRequest.max_steps` explicitly; this constant is only the
#: fallback when they don't.
DEFAULT_MAX_STEPS = 40


class ProviderError(Exception):
    """Raised when the underlying coding provider fails to produce a
    result at all (e.g. the agentic-coding-tool subprocess/session could
    not be started, crashed, or timed out before returning any usable
    result). Callers should generally treat this as a retryable
    condition."""


class MalformedResponseError(Exception):
    """Raised when the underlying coding provider *did* run its agentic
    loop to some conclusion, but the outcome could not be parsed/
    validated into a `CodingResult` (e.g. the provider's own working tree
    was left in an inconsistent state, or its final verdict doesn't
    satisfy this schema)."""


class TerminationReason(str, Enum):
    """Why a provider's bounded agentic loop stopped. Small, deliberately
    provider-agnostic vocabulary -- providers map their own SDK-specific
    stop reasons onto these three rather than leaking vendor terminology
    into `CodingResult`."""

    COMPLETED = "completed"
    STEP_BUDGET_EXHAUSTED = "step_budget_exhausted"
    PROVIDER_REPORTED_FAILURE = "provider_reported_failure"


def _nonempty_str(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise ValueError("must not be empty")
    return value


class CodingRequest(BaseModel):
    """Input payload to `CodingCapability.execute()`: the task/context
    payload, target working-tree path, and allowed-tool/command policy
    described in `docs/architecture/v1_architecture.md` section 3."""

    task_title: str
    task_summary: str
    functional_requirements: List[str] = Field(default_factory=list)
    non_functional_requirements: List[str] = Field(default_factory=list)
    acceptance_criteria: List[str] = Field(default_factory=list)
    tech_stack: List[str] = Field(default_factory=list)
    components_affected: List[str] = Field(default_factory=list)
    #: Derived from the approved UX package's specification, when a UX
    #: package is present (`DeveloperAgentInput.ux_artifact_package` is
    #: omitted entirely for workflows that never required UX design --
    #: this list is simply empty in that case, not a sentinel value).
    ux_notes: List[str] = Field(default_factory=list)
    #: Merged org -> project Standards Context (section 9.1), injected as
    #: plain text the same way it is injected into every other agent's
    #: prompt -- never retrieved, never provider-specific.
    standards_instructions: str = ""
    standards_skills: List[str] = Field(default_factory=list)

    #: Path to an *already-isolated* Git worktree the caller created off
    #: the target repository (see module docstring). The provider must
    #: never read or write outside this path.
    working_tree_path: str
    #: Read-only reference for diff purposes only (e.g. `git diff
    #: base_branch...HEAD` to compute `CodingResult.files_changed`). The
    #: provider never checks this branch out or mutates it.
    base_branch: str = "main"

    #: SDK/tool-level allow-list (e.g. `["Read", "Write", "Edit", "Bash"]`
    #: for the Claude Agent SDK provider). A request field, not a
    #: hardcoded default -- different target repos need different
    #: allow-lists (section 10).
    allowed_tools: List[str]
    #: Shell-command allow-list enforced within whatever tool grants
    #: command execution (e.g. `["git", "npm", "pytest"]`, mirroring
    #: section 10's example list). Empty means the provider grants no
    #: shell command execution at all, even if a command-capable tool
    #: (e.g. `Bash`) is present in `allowed_tools`.
    allowed_commands: List[str] = Field(default_factory=list)

    #: Self-check commands (section 4). Empty is a valid, expected state
    #: -- see `SelfCheckResult.skipped` and the module docstring's Open
    #: Question 7 discussion.
    build_commands: List[str] = Field(default_factory=list)
    test_commands: List[str] = Field(default_factory=list)

    #: Per-call override of `DEFAULT_MAX_STEPS` (Open Question 8). `None`
    #: means "use the provider's platform-wide default".
    max_steps: Optional[int] = None

    #: Opaque, provider-specific hints the capability/provider may
    #: interpret; the Developer Agent never inspects vendor-specific keys
    #: itself (mirrors `DesignRequest.provider_policy`).
    provider_policy: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("task_title", "task_summary", "working_tree_path", "base_branch")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        return _nonempty_str(value)

    @field_validator("allowed_tools")
    @classmethod
    def _allowed_tools_nonempty(cls, value: List[str]) -> List[str]:
        if not value:
            raise ValueError(
                "allowed_tools must contain at least one entry -- an empty "
                "allow-list means the provider can take no action at all; "
                "pass an explicit list rather than relying on a hidden default"
            )
        return value

    @field_validator("max_steps")
    @classmethod
    def _max_steps_positive(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value <= 0:
            raise ValueError("max_steps must be a positive integer when set")
        return value


class SelfCheckResult(BaseModel):
    """Build/test self-check outcome, matching
    `DeveloperAgentOutput.data.self_check`'s shape in section 4.

    `build_passed`/`tests_passed` are `None` (rather than `False`) when
    the corresponding command list was empty and self-checking was
    skipped entirely -- `False` is reserved for "we ran it and it
    failed". Callers must check `skipped_reason` to distinguish "not run"
    from "passed"/"failed".
    """

    build_passed: Optional[bool] = None
    tests_passed: Optional[bool] = None
    commands_run: List[str] = Field(default_factory=list)
    skipped_reason: Optional[str] = None

    @classmethod
    def skipped(cls, reason: str) -> "SelfCheckResult":
        """Construct the result for Forge's documented Open Question 7
        answer: no build/test commands were configured, so self-checking
        was skipped entirely rather than silently reported as passed."""
        return cls(build_passed=None, tests_passed=None, commands_run=[], skipped_reason=reason)


#: Shared reason string so every provider reports the same skip semantics
#: (Open Question 7) rather than each inventing its own wording.
NO_SELF_CHECK_COMMANDS_REASON = (
    "no build or test commands were configured for this target repository; "
    "self-check was skipped"
)


class CodingResult(BaseModel):
    """Provider response envelope returned by `CodingCapability.execute()`:
    structured change summary + self-check results + provider response
    envelope, per `docs/architecture/v1_architecture.md` section 3. Shaped
    to map directly onto `DeveloperAgentOutput.data` (section 4) without
    the caller knowing which provider produced it."""

    branch_name: str
    files_changed: List[str] = Field(default_factory=list)
    self_check: SelfCheckResult
    provider_name: str
    #: How many bounded-loop steps the provider actually used, for
    #: observability/cost tracking against `max_steps`/`DEFAULT_MAX_STEPS`.
    steps_used: int
    terminated_reason: TerminationReason
    #: Short natural-language summary of what changed, surfaced to the
    #: human at the approval gate (section 4/6) alongside the diff.
    summary: str
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("branch_name", "provider_name", "summary")
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
    validated `CodingResult` describing an isolated, self-checked code
    change already made inside `request.working_tree_path`.
    Implementations are responsible for whatever agentic
    read/edit/run-command looping is needed to satisfy `CodingResult`
    within `request.max_steps` (or `DEFAULT_MAX_STEPS`), and must raise
    `ProviderError` or `MalformedResponseError` (never an arbitrary/
    unrelated exception) on failure -- mirroring `ReasoningCapability`'s
    and `DesignCapability`'s failure contract exactly, so callers can
    handle all three capabilities uniformly.
    """

    @abstractmethod
    def execute(self, request: CodingRequest) -> CodingResult:
        """Run a bounded coding-agent loop against `request.working_tree_path`
        and return a validated `CodingResult`.

        Raises:
            ProviderError: the provider could not produce a result at all.
            MalformedResponseError: the provider ran, but the outcome does
                not satisfy `CodingResult`.
        """
        raise NotImplementedError()
