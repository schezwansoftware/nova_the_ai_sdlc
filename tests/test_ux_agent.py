"""Tests for the UX Agent.

No network access / external credentials required.
"""
from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from ai_sdlc.agents.base import AgentRequest, AgentStatus
from ai_sdlc.agents.po.po_agent import POAgent
from ai_sdlc.agents.ux.schemas import UXOutputData
from ai_sdlc.agents.ux.ux_agent import UXAgent
from ai_sdlc.capabilities.providers.mock import MockReasoningProvider
from ai_sdlc.orchestration.orchestrator import AgentExecutionError


def _make_request(inputs, workflow_id="wf-ux-test"):
    return AgentRequest(
        request_id=str(uuid.uuid4()),
        workflow_id=workflow_id,
        agent_id="ux",
        agent_version="1.0",
        action="default",
        inputs=inputs,
    )


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


def test_valid_requirements_produce_valid_ux_output():
    agent = UXAgent()
    request = _make_request({"requirements": _SAMPLE_REQUIREMENTS})

    result = agent.execute(request)

    assert result.status == AgentStatus.COMPLETED
    assert result.data is not None
    validated = UXOutputData(**result.data)
    assert validated.flow_title
    assert validated.summary
    assert len(validated.user_flows) > 0
    assert len(validated.screens) > 0
    assert len(validated.accessibility_considerations) > 0


def test_missing_requirements_needs_clarification():
    agent = UXAgent()
    request = _make_request({})

    result = agent.execute(request)

    assert result.status == AgentStatus.NEEDS_CLARIFICATION
    assert result.questions
    assert result.data is None


def test_empty_requirements_dict_needs_clarification():
    agent = UXAgent()
    request = _make_request({"requirements": {}})

    result = agent.execute(request)

    assert result.status == AgentStatus.NEEDS_CLARIFICATION


def test_non_dict_requirements_needs_clarification():
    agent = UXAgent()
    request = _make_request({"requirements": "not a dict"})

    result = agent.execute(request)

    assert result.status == AgentStatus.NEEDS_CLARIFICATION


def test_forced_malformed_provider_output_raises_retryable_agent_execution_error():
    agent = UXAgent(reasoning=MockReasoningProvider(force_error="malformed"))
    request = _make_request({"requirements": _SAMPLE_REQUIREMENTS})

    with pytest.raises(AgentExecutionError) as exc_info:
        agent.execute(request)

    assert exc_info.value.retryable is True


def test_forced_provider_failure_raises_retryable_agent_execution_error():
    agent = UXAgent(reasoning=MockReasoningProvider(force_error="provider_failure"))
    request = _make_request({"requirements": _SAMPLE_REQUIREMENTS})

    with pytest.raises(AgentExecutionError) as exc_info:
        agent.execute(request)

    assert exc_info.value.retryable is True


def test_ux_output_data_rejects_missing_required_field():
    payload = {
        "flow_title": "CSV Export Flow",
        "summary": "User exports a report to CSV.",
        "user_flows": ["User clicks export, file downloads."],
        "screens": ["Reports page with export button."],
        # accessibility_considerations intentionally omitted
    }
    with pytest.raises(ValidationError):
        UXOutputData(**payload)


def test_ux_output_data_rejects_empty_required_list():
    payload = {
        "flow_title": "CSV Export Flow",
        "summary": "User exports a report to CSV.",
        "user_flows": [],
        "screens": ["Reports page with export button."],
        "accessibility_considerations": ["Keyboard-accessible export button."],
    }
    with pytest.raises(ValidationError):
        UXOutputData(**payload)


def test_ux_output_data_rejects_blank_string_list_item():
    payload = {
        "flow_title": "CSV Export Flow",
        "summary": "User exports a report to CSV.",
        "user_flows": ["User clicks export, file downloads."],
        "screens": ["   "],
        "accessibility_considerations": ["Keyboard-accessible export button."],
    }
    with pytest.raises(ValidationError):
        UXOutputData(**payload)


def test_requirements_to_ux_flow_is_coherent():
    """PO Agent output data feeds directly into the UX Agent, with no
    direct agent-to-agent call -- UXAgent never imports or invokes POAgent;
    it only reads request.inputs["requirements"]."""
    po_agent = POAgent()
    po_request = _make_request(
        {
            "requirement_text": (
                "Add a CSV export button to the reports page so users can "
                "download the current report as a file."
            )
        },
        workflow_id="wf-ux-flow",
    )
    po_result = po_agent.execute(po_request)
    assert po_result.status == AgentStatus.COMPLETED

    ux_agent = UXAgent()
    ux_request = _make_request({"requirements": po_result.data}, workflow_id="wf-ux-flow")
    ux_result = ux_agent.execute(ux_request)

    assert ux_result.status == AgentStatus.COMPLETED
    validated = UXOutputData(**ux_result.data)
    assert len(validated.user_flows) > 0
    assert len(validated.screens) > 0
