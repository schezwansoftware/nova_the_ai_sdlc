"""Tests for the UX Agent's progressive lo-fi/mid-fi/hi-fi visual design
artifact generation via `DesignCapability`.

`tests/test_ux_agent.py` already covers the pre-existing text-only
`UXOutputData` contract in isolation; this file covers the additive
visual-design behavior layered on top, plus the "Provider Independence
Test" required by `docs/architecture/v1_architecture.md` section 15:
"Verify that the UX Agent contract remains unchanged when different
`DesignCapability` implementations are swapped in" -- mirroring the
pattern already established in
`tests/test_capabilities_reasoning.py::test_agent_code_is_provider_independent_across_two_mock_configs`.

No network access / external credentials required anywhere in this file.
"""
from __future__ import annotations

import uuid

import pytest

from ai_sdlc.agents.base import AgentRequest, AgentStatus
from ai_sdlc.agents.ux.schemas import UXOutputData, VisualDesignPackage
from ai_sdlc.agents.ux.ux_agent import UXAgent
from ai_sdlc.capabilities.design import FidelityLevel
from ai_sdlc.capabilities.providers.design_mock import MockDesignProvider
from ai_sdlc.capabilities.providers.mock import MockReasoningProvider
from ai_sdlc.orchestration.orchestrator import AgentExecutionError

_SAMPLE_REQUIREMENTS = {
    "feature_title": "CSV Export for Reports Page",
    "summary": "Add a CSV export button that lets users download the current report as a file.",
    "functional_requirements": [
        "System shall: Allow users to export the current report to CSV via a button.",
    ],
    "non_functional_requirements": [
        "System shall satisfy: Export must complete within 2 seconds for typical report sizes.",
    ],
    "out_of_scope": ["Exporting to formats other than CSV."],
    "acceptance_criteria": ["Verify that: clicking export downloads a valid CSV file."],
}


def _make_request(inputs, workflow_id="wf-ux-design-test"):
    return AgentRequest(
        request_id=str(uuid.uuid4()),
        workflow_id=workflow_id,
        agent_id="ux",
        agent_version="1.0",
        action="default",
        inputs=inputs,
    )


def test_ux_agent_produces_visual_design_package_alongside_ux_spec():
    agent = UXAgent()
    request = _make_request({"requirements": _SAMPLE_REQUIREMENTS})

    result = agent.execute(request)

    assert result.status == AgentStatus.COMPLETED
    assert result.data is not None

    # The pre-existing text-only contract remains intact and unchanged.
    ux_data = UXOutputData(**result.data)
    assert ux_data.flow_title
    assert len(ux_data.screens) > 0

    # New: progressive visual design artifacts, additive on top.
    assert "visual_designs" in result.data
    assert result.data["design_package_status"] == "DRAFT"
    package = VisualDesignPackage(**result.data["visual_designs"])
    assert package.lo_fi
    assert package.mid_fi
    assert package.hi_fi

    for artifact in package.lo_fi:
        assert artifact.fidelity == FidelityLevel.LO_FI
    for artifact in package.mid_fi:
        assert artifact.fidelity == FidelityLevel.MID_FI
    for artifact in package.hi_fi:
        assert artifact.fidelity == FidelityLevel.HI_FI

    # Every screen from the UX spec should have design coverage.
    lo_fi_screens = {a.screen_ref for a in package.lo_fi}
    assert lo_fi_screens == set(ux_data.screens)


def test_ux_agent_respects_caller_supplied_fidelity_levels():
    agent = UXAgent()
    request = _make_request(
        {"requirements": _SAMPLE_REQUIREMENTS, "fidelity_levels": ["lo_fi"]}
    )

    result = agent.execute(request)

    assert result.status == AgentStatus.COMPLETED
    package = VisualDesignPackage(**result.data["visual_designs"])
    assert package.lo_fi
    assert package.mid_fi == []
    assert package.hi_fi == []


def test_ux_agent_ignores_invalid_fidelity_levels_and_falls_back_to_all():
    agent = UXAgent()
    request = _make_request(
        {"requirements": _SAMPLE_REQUIREMENTS, "fidelity_levels": ["not-a-real-level"]}
    )

    result = agent.execute(request)

    assert result.status == AgentStatus.COMPLETED
    package = VisualDesignPackage(**result.data["visual_designs"])
    assert package.lo_fi and package.mid_fi and package.hi_fi


def test_missing_requirements_still_short_circuits_before_any_capability_call():
    agent = UXAgent()
    request = _make_request({})

    result = agent.execute(request)

    assert result.status == AgentStatus.NEEDS_CLARIFICATION
    assert result.data is None


def test_forced_design_provider_failure_raises_retryable_agent_execution_error():
    agent = UXAgent(design=MockDesignProvider(force_error="provider_failure"))
    request = _make_request({"requirements": _SAMPLE_REQUIREMENTS})

    with pytest.raises(AgentExecutionError) as exc_info:
        agent.execute(request)

    assert exc_info.value.retryable is True


def test_forced_design_malformed_response_raises_retryable_agent_execution_error():
    agent = UXAgent(design=MockDesignProvider(force_error="malformed"))
    request = _make_request({"requirements": _SAMPLE_REQUIREMENTS})

    with pytest.raises(AgentExecutionError) as exc_info:
        agent.execute(request)

    assert exc_info.value.retryable is True


def test_reasoning_failure_short_circuits_before_design_capability_is_called():
    """If the reasoning call fails, the design capability must never be
    invoked -- there is no UX spec yet to derive a design request from."""

    class _ExplodingDesignProvider:
        def generate(self, request):  # pragma: no cover - must never run
            raise AssertionError("DesignCapability.generate() must not be called")

    agent = UXAgent(
        reasoning=MockReasoningProvider(force_error="provider_failure"),
        design=_ExplodingDesignProvider(),
    )
    request = _make_request({"requirements": _SAMPLE_REQUIREMENTS})

    with pytest.raises(AgentExecutionError):
        agent.execute(request)


# -- Provider Independence Test ------------------------------------------
#
# docs/architecture/v1_architecture.md section 15: "Provider Independence
# Tests: Verify that the UX Agent contract remains unchanged when different
# `DesignCapability` implementations are swapped in."


class _AlternateMockDesignProvider(MockDesignProvider):
    """A second, differently-configured `DesignCapability` implementation.

    Only one real (mock) provider ships in this codebase for V1, exactly
    like `ReasoningCapability`'s existing precedent in
    `test_capabilities_reasoning.py::test_agent_code_is_provider_independent_across_two_mock_configs`,
    which also proves independence by swapping *configurations* of the
    same underlying class rather than requiring a second full vendor
    implementation. Subclassing here additionally proves the UX Agent
    only depends on the `DesignCapability` interface, not the concrete
    `MockDesignProvider` class -- swapping in this subclass (with
    different synthetic content) does not require any UX Agent code
    change.
    """

    def _describe(self, fidelity, screen, request):
        return f"[alternate-provider] {fidelity.value} concept for {screen}"


def test_ux_agent_contract_is_stable_across_swapped_design_providers():
    request = _make_request({"requirements": _SAMPLE_REQUIREMENTS})

    default_agent = UXAgent(design=MockDesignProvider())
    default_result = default_agent.execute(request)

    alternate_agent = UXAgent(design=_AlternateMockDesignProvider())
    alternate_result = alternate_agent.execute(request)

    for result in (default_result, alternate_result):
        assert result.status == AgentStatus.COMPLETED
        UXOutputData(**result.data)  # base contract unchanged
        package = VisualDesignPackage(**result.data["visual_designs"])
        assert package.lo_fi and package.mid_fi and package.hi_fi
        assert result.data["design_package_status"] == "DRAFT"

    # The two providers produced different artifact content (proving the
    # swap actually took effect), while the agent's structural contract
    # (schema-valid keys/shape) stayed identical either way.
    default_descriptions = {
        a["description"] for a in default_result.data["visual_designs"]["hi_fi"]
    }
    alternate_descriptions = {
        a["description"] for a in alternate_result.data["visual_designs"]["hi_fi"]
    }
    assert default_descriptions != alternate_descriptions


def test_ux_agent_stays_provider_independent_across_two_design_failure_configs():
    """Same proof as the reasoning-capability precedent, applied to
    DesignCapability: identical agent code path, only the injected
    provider configuration differs."""
    request = _make_request({"requirements": _SAMPLE_REQUIREMENTS})

    healthy_agent = UXAgent(design=MockDesignProvider())
    result = healthy_agent.execute(request)
    assert result.status == AgentStatus.COMPLETED

    failing_agent = UXAgent(design=MockDesignProvider(force_error="provider_failure"))
    with pytest.raises(AgentExecutionError) as exc_info:
        failing_agent.execute(request)
    assert exc_info.value.retryable is True
