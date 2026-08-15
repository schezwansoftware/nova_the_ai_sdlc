"""Unit tests for `ai_sdlc.agents.developer.developer_agent.DeveloperAgent`.

Uses `MockCodingProvider` (never touches a real coding-agent SDK) but a
real git repository for `target_repository.workspace_path` -- the
worktree lifecycle (`ai_sdlc.agents.developer.worktree`) always runs real
`git worktree` commands regardless of which `CodingCapability` provider
is configured, so these tests need a real repo to reach a real result
rather than failing on worktree creation itself.
"""
from __future__ import annotations

import uuid

import pytest

from ai_sdlc.agents.base import AgentRequest, AgentStatus
from ai_sdlc.agents.developer.developer_agent import DeveloperAgent
from ai_sdlc.capabilities.coding import (
    CodingCapability,
    CodingRequest,
    CodingResult,
    MalformedResponseError,
    ProviderError,
    SelfCheckResult,
    TerminationReason,
)
from ai_sdlc.capabilities.providers.coding_mock import MockCodingProvider
from ai_sdlc.orchestration.orchestrator import AgentExecutionError
from tests.conftest import init_git_repo

_REQUIREMENTS = {
    "feature_title": "CSV export",
    "summary": "Add a CSV export button to the reports page.",
    "functional_requirements": ["System shall export report rows as CSV."],
    "non_functional_requirements": ["System shall respond within 2s."],
    "acceptance_criteria": ["User can download a CSV of the current report."],
}
_ARCHITECTURE_UI = {"tech_stack": ["Python", "FastAPI"], "component_changes": ["reports_api"], "requires_ui": True}
_ARCHITECTURE_NO_UI = {"tech_stack": ["Python"], "component_changes": ["batch_job"], "requires_ui": False}
_UX_DESIGN = {
    "summary": "Single export button on the reports toolbar.",
    "screens": ["Reports page"],
    "accessibility_considerations": ["Button has an aria-label."],
}


class _RecordingCodingProvider(CodingCapability):
    """Spy provider: records the `CodingRequest` it was called with and
    returns a fixed, valid `CodingResult` -- lets tests assert on task
    assembly without depending on `MockCodingProvider`'s own derivation
    logic."""

    def __init__(self):
        self.requests: list[CodingRequest] = []

    def execute(self, request: CodingRequest) -> CodingResult:
        self.requests.append(request)
        return CodingResult(
            branch_name="forge/csv-export",
            files_changed=["src/reports_api.py"],
            self_check=SelfCheckResult.skipped("no build or test commands were configured"),
            provider_name="recording_coding_provider",
            steps_used=3,
            terminated_reason=TerminationReason.COMPLETED,
            summary="Added CSV export.",
        )


def _request(inputs: dict, agent_id: str = "developer") -> AgentRequest:
    return AgentRequest(
        request_id=str(uuid.uuid4()),
        workflow_id="wf-1",
        agent_id=agent_id,
        agent_version="1.0",
        action="default",
        inputs=inputs,
    )


def _base_inputs(workspace) -> dict:
    return {
        "requirements": _REQUIREMENTS,
        "architecture": _ARCHITECTURE_UI,
        "ux_design": _UX_DESIGN,
        "target_repository": {"workspace_path": str(workspace)},
    }


# -- check_needs_clarification preconditions --------------------------------


def test_needs_clarification_when_requirements_missing(tmp_path):
    agent = DeveloperAgent(coding=MockCodingProvider())
    inputs = _base_inputs(tmp_path)
    del inputs["requirements"]

    result = agent.execute(_request(inputs))

    assert result.status == AgentStatus.NEEDS_CLARIFICATION
    assert "requirements" in result.questions[0].lower()


def test_needs_clarification_when_architecture_missing(tmp_path):
    agent = DeveloperAgent(coding=MockCodingProvider())
    inputs = _base_inputs(tmp_path)
    del inputs["architecture"]

    result = agent.execute(_request(inputs))

    assert result.status == AgentStatus.NEEDS_CLARIFICATION
    assert "architecture" in result.questions[0].lower()


def test_needs_clarification_when_ux_required_but_missing(tmp_path):
    agent = DeveloperAgent(coding=MockCodingProvider())
    inputs = _base_inputs(tmp_path)
    del inputs["ux_design"]  # architecture.requires_ui is True here

    result = agent.execute(_request(inputs))

    assert result.status == AgentStatus.NEEDS_CLARIFICATION
    assert "ux design" in result.questions[0].lower()


def test_no_clarification_when_ux_not_required_and_missing(tmp_path):
    workspace = init_git_repo(tmp_path / "repo")
    agent = DeveloperAgent(coding=MockCodingProvider())
    inputs = _base_inputs(workspace)
    inputs["architecture"] = _ARCHITECTURE_NO_UI
    del inputs["ux_design"]

    result = agent.execute(_request(inputs))

    assert result.status == AgentStatus.NEEDS_APPROVAL


def test_needs_clarification_when_target_repository_missing(tmp_path):
    agent = DeveloperAgent(coding=MockCodingProvider())
    inputs = _base_inputs(tmp_path)
    del inputs["target_repository"]

    result = agent.execute(_request(inputs))

    assert result.status == AgentStatus.NEEDS_CLARIFICATION
    assert "workspace_path" in result.questions[0].lower() or "target repository" in result.questions[0].lower()


# -- successful execute() flow -----------------------------------------------


def test_execute_success_returns_needs_approval_with_coding_result_data(tmp_path):
    workspace = init_git_repo(tmp_path / "repo")
    agent = DeveloperAgent(coding=MockCodingProvider())

    result = agent.execute(_request(_base_inputs(workspace)))

    assert result.status == AgentStatus.NEEDS_APPROVAL
    assert result.decision is not None and result.decision.approval_required is True
    assert result.artifact is not None and result.artifact.path == ".ai-sdlc/implementation.json"
    assert result.data["provider_name"] == "mock_coding_provider"
    assert result.data["branch_name"]
    assert result.data["self_check"]["skipped_reason"]  # no build/test commands configured -> skip, not fail


def test_execute_assembles_coding_request_from_structured_inputs(tmp_path):
    workspace = init_git_repo(tmp_path / "repo")
    spy = _RecordingCodingProvider()
    agent = DeveloperAgent(coding=spy)

    agent.execute(_request(_base_inputs(workspace)))

    assert len(spy.requests) == 1
    req = spy.requests[0]
    assert req.task_title == _REQUIREMENTS["feature_title"]
    assert _REQUIREMENTS["summary"] in req.task_summary
    assert req.functional_requirements == _REQUIREMENTS["functional_requirements"]
    assert req.acceptance_criteria == _REQUIREMENTS["acceptance_criteria"]
    assert req.tech_stack == _ARCHITECTURE_UI["tech_stack"]
    assert req.components_affected == _ARCHITECTURE_UI["component_changes"]
    assert _UX_DESIGN["summary"] in req.ux_notes
    assert any("Reports page" in note for note in req.ux_notes)
    assert req.base_branch  # detected from the real repo, not hardcoded
    assert req.working_tree_path.endswith("wf-1")


def test_execute_uses_v1_default_allow_list_when_not_overridden(tmp_path):
    workspace = init_git_repo(tmp_path / "repo")
    spy = _RecordingCodingProvider()
    agent = DeveloperAgent(coding=spy)

    agent.execute(_request(_base_inputs(workspace)))

    req = spy.requests[0]
    assert req.allowed_tools == ["Read", "Write", "Edit", "Bash"]
    assert req.allowed_commands == ["git", "mvn", "gradle", "npm", "pytest"]
    assert req.build_commands == []
    assert req.test_commands == []


def test_execute_respects_caller_supplied_allow_list_override(tmp_path):
    workspace = init_git_repo(tmp_path / "repo")
    spy = _RecordingCodingProvider()
    agent = DeveloperAgent(coding=spy)
    inputs = _base_inputs(workspace)
    inputs["allowed_tools"] = ["Read", "Bash"]
    inputs["allowed_commands"] = ["git"]
    inputs["build_commands"] = ["npm run build"]
    inputs["test_commands"] = ["npm test"]

    agent.execute(_request(inputs))

    req = spy.requests[0]
    assert req.allowed_tools == ["Read", "Bash"]
    assert req.allowed_commands == ["git"]
    assert req.build_commands == ["npm run build"]
    assert req.test_commands == ["npm test"]


def test_execute_folds_revision_feedback_into_task_summary(tmp_path):
    """No dedicated `revision_feedback` field exists on `CodingRequest`
    (todo.md flags this as an open question against the canonical
    `coding.py` interface) -- this agent's resolution is folding it into
    the task description, mirroring how Orion threads a rejected
    approval's feedback onto `wf.inputs["revision_feedback"]`."""
    workspace = init_git_repo(tmp_path / "repo")
    spy = _RecordingCodingProvider()
    agent = DeveloperAgent(coding=spy)
    inputs = _base_inputs(workspace)
    inputs["revision_feedback"] = "The export button needs a loading spinner while the file generates."

    agent.execute(_request(inputs))

    assert "loading spinner" in spy.requests[0].task_summary


def test_execute_reuses_and_resets_the_same_worktree_across_calls(tmp_path):
    """Every legitimate re-invocation (a retryable provider failure, or a
    retried revision) must not accumulate worktrees or leak the previous
    attempt's state -- see worktree.py's "Re-entry" docstring section."""
    workspace = init_git_repo(tmp_path / "repo")
    agent = DeveloperAgent(coding=MockCodingProvider())

    first = agent.execute(_request(_base_inputs(workspace)))
    second = agent.execute(_request(_base_inputs(workspace)))

    assert first.status == AgentStatus.NEEDS_APPROVAL
    assert second.status == AgentStatus.NEEDS_APPROVAL
    assert first.data["branch_name"] == second.data["branch_name"]


# -- failure handling ---------------------------------------------------------


def test_execute_raises_retryable_agent_execution_error_on_provider_failure(tmp_path):
    workspace = init_git_repo(tmp_path / "repo")
    agent = DeveloperAgent(coding=MockCodingProvider(force_error="provider_failure"))

    with pytest.raises(AgentExecutionError) as exc_info:
        agent.execute(_request(_base_inputs(workspace)))
    assert exc_info.value.retryable is True


def test_execute_raises_retryable_agent_execution_error_on_malformed_response(tmp_path):
    workspace = init_git_repo(tmp_path / "repo")
    agent = DeveloperAgent(coding=MockCodingProvider(force_error="malformed"))

    with pytest.raises(AgentExecutionError) as exc_info:
        agent.execute(_request(_base_inputs(workspace)))
    assert exc_info.value.retryable is True


def test_execute_raises_retryable_agent_execution_error_when_target_repo_is_not_a_git_repo(tmp_path):
    not_a_repo = tmp_path / "plain-dir"
    not_a_repo.mkdir()
    agent = DeveloperAgent(coding=MockCodingProvider())
    inputs = _base_inputs(not_a_repo)

    with pytest.raises(AgentExecutionError) as exc_info:
        agent.execute(_request(inputs))
    assert exc_info.value.retryable is True
