"""UX Agent.

Consumes a structured requirements dict (e.g. what
`POAgentOutputData.model_dump()` produces) via `request.inputs["requirements"]`
and produces:
  1. a structured UX design (`UXOutputData`): primary user flow(s), key
     screens/views, and accessibility considerations -- via
     `ReasoningCapability`, exactly as before this change; and
  2. progressive lo-fi/mid-fi/hi-fi visual design artifacts
     (`VisualDesignPackage`) derived from that UX design -- via
     `DesignCapability`, a new capability call added in this change (see
     `capabilities/design.py`).

UX design is treated as parallel to Architecture -- both derived directly
from requirements, not chained off Architecture's output. This agent never
imports or calls `POAgent`, `ArchitectureAgent`, or any other agent
directly -- it only reads from `request.inputs`, exactly as Orion will
provide it once wired into the workflow graph. It is stateless: it never
touches `.ai-sdlc/`, never manages workflow transitions or approvals, and
never decides artifact revision/approval status itself -- every artifact
this agent produces starts as `DRAFT` (see `DesignPackageStatus`); the
review/revision loop is Core/Orion's job
(`docs/architecture/v1_architecture.md` section 6, "UX Revision & Feedback
Loop").

Why `execute()` is overridden here instead of reusing
`SpecialistAgent.execute()` unmodified: the shared flow in `framework.py`
calls exactly one capability (`ReasoningCapability`) and returns its output
directly as `AgentResult.data`. The UX Agent needs a second, structurally
different capability call (`DesignCapability`) whose result must be merged
alongside the first before returning. Rather than changing the shared
`SpecialistAgent.execute()`/`build_result_extras()` contract (which would
touch every other agent's code path for a need only the UX Agent has right
now), this override stays local to `ux_agent.py` and reuses every other
piece of `SpecialistAgent` as-is (`__init__`, `check_needs_clarification`
hook, `build_prompt` hook, `output_schema`, the `reasoning` capability, and
the `AgentExecutionError`-on-failure contract).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ai_sdlc.agents.base import AgentRequest, AgentResult, AgentStatus
from ai_sdlc.agents.framework import SpecialistAgent
from ai_sdlc.agents.ux.prompts import build_ux_prompt
from ai_sdlc.agents.ux.schemas import UXOutputData, VisualDesignPackage
from ai_sdlc.capabilities.design import DesignCapability
from ai_sdlc.capabilities.design import DesignPackageStatus
from ai_sdlc.capabilities.design import DesignRequest
from ai_sdlc.capabilities.design import DesignResponse
from ai_sdlc.capabilities.design import FidelityLevel
from ai_sdlc.capabilities.design import MalformedResponseError as DesignMalformedResponseError
from ai_sdlc.capabilities.design import ProviderError as DesignProviderError
from ai_sdlc.capabilities.providers.design_mock import MockDesignProvider
from ai_sdlc.capabilities.reasoning import MalformedResponseError, ProviderError, ReasoningCapability

# Same deliberate reuse of Orion's existing retry-loop signal documented in
# `framework.py` -- see that module's module docstring/import comment for
# why this does not create an import cycle.
from ai_sdlc.orchestration.orchestrator import AgentExecutionError

_ALL_FIDELITIES = [FidelityLevel.LO_FI, FidelityLevel.MID_FI, FidelityLevel.HI_FI]


def _parse_fidelity_levels(raw: Any) -> List[FidelityLevel]:
    """Parse an optional `request.inputs["fidelity_levels"]` override into
    a list of `FidelityLevel`. Falls back to all three levels if `raw` is
    missing, not a list, or contains no recognizable values -- this is a
    caller convenience input, not a hard contract, so it fails open rather
    than raising.

    This is how Orion/Core would tell the (stateless) UX Agent which
    fidelity level(s) to produce for a given revision, mirroring the
    existing `revision_feedback` input-threading pattern described in
    `docs/architecture/v1_architecture.md` section 6 -- the agent itself
    never tracks or decides progression state.
    """
    if not raw or not isinstance(raw, (list, tuple)):
        return list(_ALL_FIDELITIES)

    parsed: List[FidelityLevel] = []
    for item in raw:
        try:
            level = FidelityLevel(str(item).strip().upper())
        except ValueError:
            continue
        if level not in parsed:
            parsed.append(level)
    return parsed or list(_ALL_FIDELITIES)


def _build_design_request(ux_data: UXOutputData, inputs: Dict[str, Any]) -> DesignRequest:
    provider_policy = inputs.get("design_provider_policy")
    return DesignRequest(
        flow_title=ux_data.flow_title,
        summary=ux_data.summary,
        screens=ux_data.screens,
        user_flows=ux_data.user_flows,
        fidelities=_parse_fidelity_levels(inputs.get("fidelity_levels")),
        provider_policy=provider_policy if isinstance(provider_policy, dict) else {},
    )


def _group_by_fidelity(design_response: DesignResponse) -> VisualDesignPackage:
    buckets: Dict[FidelityLevel, list] = {level: [] for level in _ALL_FIDELITIES}
    for artifact in design_response.artifacts:
        buckets[artifact.fidelity].append(artifact)
    return VisualDesignPackage(
        lo_fi=buckets[FidelityLevel.LO_FI],
        mid_fi=buckets[FidelityLevel.MID_FI],
        hi_fi=buckets[FidelityLevel.HI_FI],
    )


class UXAgent(SpecialistAgent):
    output_schema = UXOutputData

    def __init__(
        self,
        reasoning: Optional[ReasoningCapability] = None,
        design: Optional[DesignCapability] = None,
    ):
        super().__init__(agent_id="ux", version="1.0", reasoning=reasoning)
        # Same zero-arg-constructible requirement as `reasoning` in
        # `SpecialistAgent.__init__` (required by
        # `AgentRegistry._load_impl`, which calls `cls()`), while still
        # allowing a caller/test to inject a specific `DesignCapability`
        # (e.g. one configured with force_error=...) to prove this agent
        # is provider-independent for both capabilities it uses.
        self.design: DesignCapability = design or MockDesignProvider()

    def check_needs_clarification(self, request: AgentRequest) -> Optional[str]:
        inputs: Dict[str, Any] = request.inputs or {}
        requirements = inputs.get("requirements")
        if not requirements or not isinstance(requirements, dict):
            return (
                "No structured requirements were provided. Please run/complete the "
                "PO Agent stage first, or supply a requirements object to design UX against."
            )
        return None

    def build_prompt(self, request: AgentRequest) -> str:
        inputs: Dict[str, Any] = request.inputs or {}
        requirements: Dict[str, Any] = inputs.get("requirements") or {}
        return build_ux_prompt(requirements, sage_context=inputs.get("sage_context"))

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

        prompt = self.build_prompt(request)
        try:
            ux_data: UXOutputData = self.reasoning.complete(
                prompt, output_schema=self.output_schema
            )
        except (MalformedResponseError, ProviderError) as exc:
            raise AgentExecutionError(str(exc), retryable=True) from exc

        # Same model-driven clarification path as `SpecialistAgent.execute()`
        # (`framework.py`) -- duplicated here rather than shared because this
        # `execute()` is itself already a full override (see the module
        # docstring for why). A UX flow the reasoning call itself couldn't
        # resolve has nothing meaningful to generate visuals for, so this
        # returns before ever calling `DesignCapability`.
        if ux_data.needs_clarification:
            return AgentResult(
                request_id=request.request_id,
                workflow_id=request.workflow_id,
                agent_id=self.agent_id,
                status=AgentStatus.NEEDS_CLARIFICATION,
                questions=[ux_data.clarification_question],
            )

        # Same shape as the needs_clarification branch above, but resolved
        # automatically by Sage rather than paused for a human -- see
        # Orchestrator.invoke_agent_for_stage's NEEDS_CONTEXT branch. Also
        # returns before calling DesignCapability, for the same reason: a
        # UX flow still missing context has nothing meaningful to generate
        # visuals for yet.
        if ux_data.needs_context:
            return AgentResult(
                request_id=request.request_id,
                workflow_id=request.workflow_id,
                agent_id=self.agent_id,
                status=AgentStatus.NEEDS_CONTEXT,
                context_query=ux_data.context_query,
            )

        inputs: Dict[str, Any] = request.inputs or {}
        design_request = _build_design_request(ux_data, inputs)
        try:
            design_response = self.design.generate(design_request)
        except (DesignMalformedResponseError, DesignProviderError) as exc:
            raise AgentExecutionError(str(exc), retryable=True) from exc

        visual_designs = _group_by_fidelity(design_response)

        data = {
            **ux_data.model_dump(),
            "visual_designs": visual_designs.model_dump(),
            "design_package_status": DesignPackageStatus.DRAFT.value,
        }

        return AgentResult(
            request_id=request.request_id,
            workflow_id=request.workflow_id,
            agent_id=self.agent_id,
            status=AgentStatus.COMPLETED,
            data=data,
        )
