"""Developer Agent (Forge).

Turns an already-approved spec (requirements + architecture + UX, threaded
forward via `wf.inputs` exactly as Architecture/UX already receive each
other's prior output) into a real, isolated, self-checked code change,
by assembling that spec into a `CodingRequest` and calling
`CodingCapability` -- see `docs/architecture/v1_architecture.md` section 4's
"Developer Agent Contract" for the full contract this implements.

## Scope of this pass: "stop at approved diff"

This agent creates the isolated worktree, calls `CodingCapability`, and
requests human approval for the resulting diff through the existing
generic approval gate (`AgentStatus.NEEDS_APPROVAL` -> Orion's
`WAITING_FOR_APPROVAL`/`submit_approval`) -- it does **not** push the
branch or open a pull request. That's a deliberately deferred follow-up
pass: `todo.md`'s Forge follow-up section flags the push/PR trigger
mechanism (a second call? a separate capability method? Nexus-owned?) as
genuinely undecided, and it needs a real GitHub token/target repo to
exercise anyway. The approved worktree is left on disk (see
`developer.worktree.ensure_clean_worktree` / the module docstring there)
for that follow-up pass to push and then clean up.

## Why this subclasses `Agent`, not `SpecialistAgent`

`SpecialistAgent` (`agents/framework.py`) implements "build a prompt, call
`ReasoningCapability.complete()`, validate structured output" -- the right
shared shape for PO/Architecture, and close enough for UX (which overrides
`execute()` to layer on a second, `DesignCapability` call). This agent's
primary deliverable comes from `CodingCapability`, not from a
`ReasoningCapability`-validated schema at all: task assembly here is
deterministic string/list building from already-structured
requirements/architecture/UX dicts, not a judgment call that benefits from
an LLM. `ReasoningCapability` is part of this agent's Tier 3 capability
*grant* (architecture doc section 8's capability tiers describe what an
agent is permitted to call, not what it must call every time), but V1 has
no concrete use for it, so it isn't wired in here -- adding it later for a
real need (e.g. a smarter task-assembly step) is a additive change, not a
contract break.

## UX handoff gating: a deliberately incomplete enforcement of section 6

Section 6's "UX -> Developer Agent Handoff Contract" says the Developer
Agent stage cannot begin unless `design_package_status == "APPROVED"".
This agent only checks that `ux_design` is *present* when Architecture
classified the requirement as needing UI, not that its
`design_package_status` is `APPROVED` -- enforcing the stricter rule
today would make this agent permanently unreachable, since nothing
upstream (no UX artifact persistence, no approval-gating on the UX node
itself -- `UXAgent.execute()` always returns `COMPLETED` with
`design_package_status: "DRAFT"`, never `NEEDS_APPROVAL`) has any path to
ever setting that field to `APPROVED` yet; that's Orion/Core's
still-unbuilt UX artifact persistence + revision-loop wiring (see
`todo.md`'s "Orion / Core -- UX_DESIGN wiring follow-up"). Tightening this
check is a natural follow-up once that lands, not something this pass can
honestly enforce.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ai_sdlc.agents.base import (
    Agent,
    AgentDecision,
    AgentRequest,
    AgentResult,
    AgentStatus,
    ArtifactRef,
)
from ai_sdlc.agents.developer.worktree import WorktreeError, detect_base_branch, ensure_clean_worktree
from ai_sdlc.capabilities.coding import (
    CodingCapability,
    CodingRequest,
    MalformedResponseError,
    ProviderError,
)
from ai_sdlc.capabilities.providers.coding_factory import get_default_coding_provider

# Same deliberate reuse of Orion's existing retry-loop signal documented in
# `framework.py`/`ux_agent.py` -- see `framework.py`'s module docstring for
# why this does not create an import cycle.
from ai_sdlc.orchestration.orchestrator import AgentExecutionError

#: V1 default allow-list, matching the exact example list
#: `docs/architecture/v1_architecture.md` section 10 ("Tool Sandboxing")
#: gives. A Standards Context Layer (section 9.1) per-tech-stack override
#: would be the correct long-term source for this (not built yet -- see
#: the module docstring); a caller may still override per-call via
#: `request.inputs["allowed_tools"]`/`["allowed_commands"]` in the
#: meantime.
_DEFAULT_ALLOWED_TOOLS: List[str] = ["Read", "Write", "Edit", "Bash"]
_DEFAULT_ALLOWED_COMMANDS: List[str] = ["git", "mvn", "gradle", "npm", "pytest"]


class DeveloperAgent(Agent):
    def __init__(self, coding: Optional[CodingCapability] = None):
        super().__init__(agent_id="developer", version="1.0")
        # Same zero-arg-constructible requirement as every other concrete
        # agent (`AgentRegistry._load_impl` calls `cls()`), while still
        # allowing a caller/test to inject a specific `CodingCapability`
        # (e.g. `MockCodingProvider(force_error=...)`) to prove this agent
        # is provider-independent.
        self.coding: CodingCapability = coding or get_default_coding_provider()

    # -- preconditions (cheap, no capability call) ------------------------

    def check_needs_clarification(self, request: AgentRequest) -> Optional[str]:
        inputs: Dict[str, Any] = request.inputs or {}

        requirements = inputs.get("requirements")
        if not requirements or not isinstance(requirements, dict):
            return (
                "No structured requirements were provided. Please run/complete the "
                "PO Agent stage first, or supply a requirements object to build against."
            )

        architecture = inputs.get("architecture")
        if not architecture or not isinstance(architecture, dict):
            return (
                "No architecture output was provided. Please run/complete the "
                "Architecture Agent stage first, or supply an architecture object to build against."
            )

        if architecture.get("requires_ui", True):
            ux_design = inputs.get("ux_design")
            if not ux_design or not isinstance(ux_design, dict):
                return (
                    "Architecture classified this requirement as needing a UX design, "
                    "but none was provided. Please run/complete the UX Agent stage first."
                )

        target_repository = inputs.get("target_repository")
        if not isinstance(target_repository, dict) or not target_repository.get("workspace_path"):
            return (
                "No target repository workspace_path was provided; the Developer Agent "
                "has nowhere to create an isolated working tree."
            )

        return None

    # -- shared execute() flow --------------------------------------------

    def execute(self, request: AgentRequest) -> AgentResult:
        question = self.check_needs_clarification(request)
        if question:
            return AgentResult(
                request_id=request.request_id,
                workflow_id=request.workflow_id,
                agent_id=self.agent_id,
                status=AgentStatus.NEEDS_CLARIFICATION,
                questions=[question],
            )

        inputs: Dict[str, Any] = request.inputs or {}
        requirements: Dict[str, Any] = inputs["requirements"]
        architecture: Dict[str, Any] = inputs["architecture"]
        raw_ux = inputs.get("ux_design")
        ux_design: Optional[Dict[str, Any]] = raw_ux if isinstance(raw_ux, dict) else None
        target_repository: Dict[str, Any] = inputs["target_repository"]
        workspace_path = Path(target_repository["workspace_path"])

        base_branch = target_repository.get("base_branch")
        try:
            if not base_branch:
                base_branch = detect_base_branch(workspace_path)
            worktree = ensure_clean_worktree(workspace_path, self.agent_id, request.workflow_id, base_branch)
        except WorktreeError as exc:
            raise AgentExecutionError(str(exc), retryable=True) from exc

        coding_request = self._build_coding_request(
            requirements, architecture, ux_design, inputs, worktree, base_branch
        )

        try:
            result = self.coding.execute(coding_request)
        except (ProviderError, MalformedResponseError) as exc:
            # Controlled agent failure, mirroring SpecialistAgent.execute()'s
            # ReasoningCapability handling exactly: never a bare/unhandled
            # exception, reuse Orion's existing retry mechanism. A retry
            # re-enters this same execute() call, which resets the worktree
            # (ensure_clean_worktree) before trying again -- see
            # developer.worktree's module docstring's "Re-entry" section.
            raise AgentExecutionError(str(exc), retryable=True) from exc

        # The diff is real and already committed inside `worktree` at this
        # point (the coding provider's own prompt instructs it to commit as
        # it goes -- see claude_sdk.py's `_build_prompt`); this only
        # requests human approval before anything is pushed anywhere. Full,
        # final `data` is attached to the approval request itself, not
        # produced by a later call -- see langgraph_runner.py's
        # `resume_after_approval` for why approval no longer re-invokes the
        # requesting agent, which makes that the only correct place to put
        # it.
        return AgentResult(
            request_id=request.request_id,
            workflow_id=request.workflow_id,
            agent_id=self.agent_id,
            status=AgentStatus.NEEDS_APPROVAL,
            data=result.model_dump(),
            artifact=ArtifactRef(type="implementation", path=".ai-sdlc/implementation.json"),
            decision=AgentDecision(status="ready_for_approval", approval_required=True),
        )

    # -- task assembly ------------------------------------------------------

    def _build_coding_request(
        self,
        requirements: Dict[str, Any],
        architecture: Dict[str, Any],
        ux_design: Optional[Dict[str, Any]],
        inputs: Dict[str, Any],
        worktree: Path,
        base_branch: str,
    ) -> CodingRequest:
        task_summary_parts: List[str] = [str(requirements.get("summary") or "")]

        rationale = architecture.get("rationale")
        if rationale:
            task_summary_parts.append(f"Architecture rationale: {rationale}")

        # Revision loop: Orion threads a rejected approval's feedback onto
        # wf.inputs["revision_feedback"] (orchestrator.py's
        # resume_workflow_after_approval, rejected branch), the same
        # accumulated-wf.inputs mechanism already used for clarification
        # answers, mirroring the UX Agent's documented revision-loop
        # pattern (architecture doc section 6). CodingRequest itself has no
        # dedicated revision_feedback field (todo.md flagged this as an
        # open question against the canonical coding.py interface) -- this
        # is this agent's resolution: fold it into the task description
        # rather than inventing a capability-level field.
        revision_feedback = inputs.get("revision_feedback")
        if revision_feedback:
            task_summary_parts.append(
                "A previous attempt at this change was reviewed by a human and "
                f"rejected. Address this feedback in your new attempt: {revision_feedback}"
            )

        ux_notes: List[str] = []
        if ux_design:
            if ux_design.get("summary"):
                ux_notes.append(str(ux_design["summary"]))
            ux_notes.extend(f"Screen: {screen}" for screen in ux_design.get("screens", []))
            ux_notes.extend(
                f"Accessibility: {item}" for item in ux_design.get("accessibility_considerations", [])
            )

        return CodingRequest(
            task_title=str(requirements.get("feature_title") or "Untitled change"),
            task_summary="\n\n".join(part for part in task_summary_parts if part),
            functional_requirements=list(requirements.get("functional_requirements", [])),
            non_functional_requirements=list(requirements.get("non_functional_requirements", [])),
            acceptance_criteria=list(requirements.get("acceptance_criteria", [])),
            tech_stack=list(architecture.get("tech_stack", [])),
            components_affected=list(architecture.get("component_changes", [])),
            ux_notes=ux_notes,
            # Standards Context Layer (architecture doc section 9.1) has no
            # implementation yet anywhere in this codebase -- left blank
            # rather than guessed at, the same "simply absent" posture
            # section 6 already documents for an unrequired ux_artifact_package.
            standards_instructions="",
            standards_skills=[],
            working_tree_path=str(worktree),
            base_branch=base_branch,
            allowed_tools=list(inputs.get("allowed_tools") or _DEFAULT_ALLOWED_TOOLS),
            allowed_commands=list(inputs.get("allowed_commands") or _DEFAULT_ALLOWED_COMMANDS),
            # Empty is Open Question 7's documented, deliberate answer (see
            # coding.py's SelfCheckResult.skipped/NO_SELF_CHECK_COMMANDS_REASON)
            # for the same "no Standards Context yet" reason as above, not
            # an oversight -- a caller may still supply these explicitly.
            build_commands=list(inputs.get("build_commands") or []),
            test_commands=list(inputs.get("test_commands") or []),
        )
