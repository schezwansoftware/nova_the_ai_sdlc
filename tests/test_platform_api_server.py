import json
import threading
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from pathlib import Path

from ai_sdlc.orchestration.state import StateStore, WorkflowState
from ai_sdlc.platform.server import run_platform_server


def prepare_workspace_with_po(tmp_path: Path) -> Path:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    agents_dir = workspace / ".ai-sdlc" / "agents"
    agents_dir.mkdir(parents=True)
    metadata = {
        "agent_id": "po",
        "version": "1.0",
        "impl": "ai_sdlc.agents.po.po_agent.POAgent",
        "input_schema": "po-input-v1",
        "output_schema": "po-output-v1",
        "capabilities": ["reasoning"],
        "state_artifact": "requirements.json",
    }
    (agents_dir / "po.json").write_text(json.dumps(metadata), encoding="utf-8")
    return workspace


def _start_server(workspace: Path):
    server = run_platform_server(str(workspace), host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _http_request(server, method: str, path: str, body=None):
    conn = HTTPConnection(server.server_address[0], server.server_address[1])
    headers = {"Content-Type": "application/json"}
    body_data = json.dumps(body).encode("utf-8") if body is not None else None
    conn.request(method, path, body=body_data, headers=headers)
    response = conn.getresponse()
    data = response.read().decode("utf-8")
    conn.close()
    return response.status, json.loads(data)


def test_state_store_uses_lock_file(tmp_path: Path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    store = StateStore(workspace)
    wf = WorkflowState(workflow_id="wf-lock", current_stage="requirements")

    store.write_workflow(wf)
    assert (workspace / ".ai-sdlc" / "workflow.lock").exists()

    loaded = store.read_workflow()
    assert loaded is not None
    assert loaded.workflow_id == "wf-lock"
    assert loaded.current_stage == "requirements"


def test_state_store_handles_concurrent_writes(tmp_path: Path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    store = StateStore(workspace)

    def write_workflow(index: int) -> None:
        wf = WorkflowState(workflow_id=f"wf-{index}", current_stage="requirements")
        store.write_workflow(wf)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write_workflow, range(8)))

    loaded = store.read_workflow()
    assert loaded is not None
    assert loaded.workflow_id.startswith("wf-")
    assert loaded.current_stage == "requirements"


def test_platform_http_api_start_and_get_status(tmp_path: Path):
    workspace = prepare_workspace_with_po(tmp_path)
    server, thread = _start_server(workspace)
    try:
        status, body = _http_request(server, "POST", "/v1/workflows", {
            "initiator_id": "u1",
            "raw_requirement": "Add export functionality for customers.",
            "project_context": {"repository_name": "order-service"},
        })
        assert status == 200
        assert body["success"] is True
        workflow_id = body["data"]["workflow_id"]

        status, body = _http_request(server, "GET", f"/v1/workflows/{workflow_id}")
        assert status == 200
        assert body["success"] is True
        assert body["data"]["workflow_id"] == workflow_id
        assert body["data"]["status"] in ("RUNNING", "COMPLETED")
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_platform_http_api_start_with_requirement_clarification(tmp_path: Path):
    workspace = prepare_workspace_with_po(tmp_path)
    server, thread = _start_server(workspace)
    try:
        status, body = _http_request(server, "POST", "/v1/workflows", {
            "initiator_id": "u1",
            "raw_requirement": "Please clarify which fields are required.",
            "project_context": {"repository_name": "order-service"},
        })
        assert status == 200
        assert body["success"] is True
        workflow_id = body["data"]["workflow_id"]

        status, body = _http_request(server, "GET", f"/v1/workflows/{workflow_id}")
        assert status == 200
        assert body["success"] is True
        assert body["data"]["workflow_id"] == workflow_id
        assert body["data"]["status"] == "WAITING_FOR_CLARIFICATION"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_platform_http_api_submit_clarification_and_resume(tmp_path: Path):
    workspace = prepare_workspace_with_po(tmp_path)
    store = StateStore(workspace)
    wf = WorkflowState(
        workflow_id="wf-clarify",
        current_stage="requirements",
        initiator_id="u2",
        status="paused",
    )
    store.write_workflow(wf)

    server, thread = _start_server(workspace)
    try:
        status, body = _http_request(server, "POST", "/v1/workflows/wf-clarify/clarifications", {
            "initiator_id": "u2",
            "response_text": "Yes use CSV",
        })
        assert status == 200
        assert body["success"] is True
        assert body["data"]["workflow_id"] == "wf-clarify"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_platform_http_api_submit_approval_and_resume(tmp_path: Path):
    workspace = prepare_workspace_with_po(tmp_path)
    store = StateStore(workspace)
    wf = WorkflowState(
        workflow_id="wf-approve",
        current_stage="requirements",
        initiator_id="u3",
        status="waiting_for_approval",
        pending_approval={"stage": "requirements"},
    )
    store.write_workflow(wf)

    server, thread = _start_server(workspace)
    try:
        status, body = _http_request(server, "POST", "/v1/workflows/wf-approve/approvals", {
            "initiator_id": "u3",
            "approved": True,
            "feedback": "Looks good.",
        })
        assert status == 200
        assert body["success"] is True
        assert body["data"]["workflow_id"] == "wf-approve"
        assert body["data"]["status"] in ("RUNNING", "COMPLETED")
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_platform_http_api_reports_not_found_for_missing_workflow(tmp_path: Path):
    workspace = prepare_workspace_with_po(tmp_path)
    server, thread = _start_server(workspace)
    try:
        status, body = _http_request(server, "GET", "/v1/workflows/missing-workflow")
        assert status == 404
        assert body["success"] is False
        assert body["error"]["code"] == "WORKFLOW_NOT_FOUND"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_platform_http_api_reports_conflict_for_invalid_state_transition(tmp_path: Path):
    workspace = prepare_workspace_with_po(tmp_path)
    store = StateStore(workspace)
    wf = WorkflowState(
        workflow_id="wf-conflict",
        current_stage="requirements",
        initiator_id="u4",
        status="running",
    )
    store.write_workflow(wf)

    server, thread = _start_server(workspace)
    try:
        status, body = _http_request(server, "POST", "/v1/workflows/wf-conflict/clarifications", {
            "initiator_id": "u4",
            "response_text": "More detail please",
        })
        assert status == 409
        assert body["success"] is False
        assert body["error"]["code"] == "INVALID_STATE_TRANSITION"
    finally:
        server.shutdown()
        thread.join(timeout=5)
