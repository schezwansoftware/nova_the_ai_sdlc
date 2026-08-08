from pathlib import Path
import shutil
import pytest
from ai_sdlc.orchestration.state import StateStore, WorkflowState
from ai_sdlc.orchestration.graph import GraphRunner, Node, NodeType, Transition
from ai_sdlc.agents.base import AgentRequest, AgentResult, AgentStatus


def test_state_store_write_and_read(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    store = StateStore(workspace)

    ws = WorkflowState(workflow_id="wf-001", current_stage="requirements")
    store.write_workflow(ws)

    loaded = store.read_workflow()
    assert loaded is not None
    assert loaded.workflow_id == "wf-001"
    assert loaded.current_stage == "requirements"


def test_graph_runner_simple_flow():
    ran = []

    def start_handler(ctx):
        ctx["count"] = ctx.get("count", 0) + 1
        ran.append("start")

    def middle_handler(ctx):
        ctx["count"] += 1
        ran.append("middle")

    gr = GraphRunner()
    gr.add_node(Node("start", NodeType.ACTION, start_handler))
    gr.add_node(Node("middle", NodeType.ACTION, middle_handler))
    gr.add_node(Node("end", NodeType.ACTION, lambda ctx: ran.append("end")))

    gr.add_transition(Transition("start", "middle"))
    gr.add_transition(Transition("middle", "end"))

    res = gr.run("start", {})
    assert res["status"] == "completed"
    assert ran == ["start", "middle", "end"]


def test_agent_result_validation_schema():
    # Ensure AgentResult can be constructed with required fields and statuses
    req = AgentRequest(
        request_id="req-1",
        workflow_id="wf-1",
        agent_id="po",
        agent_version="1.0",
        action="refine_requirements",
    )

    result = AgentResult(
        request_id=req.request_id,
        workflow_id=req.workflow_id,
        agent_id=req.agent_id,
        status=AgentStatus.COMPLETED,
    )

    assert result.status == AgentStatus.COMPLETED
