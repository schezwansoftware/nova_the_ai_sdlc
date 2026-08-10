import json
import socket
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
    # The real workflow graph now also runs Architecture and UX after PO
    # (see DEFAULT_WORKFLOW_NODES in orchestration/langgraph_runner.py), so
    # any workspace that expects a full run to reach COMPLETED needs both
    # discoverable too, exactly the way `ai-sdlc init` would eventually
    # scaffold them (see todo.md's Pixel metadata blocks for the po/
    # architecture shape this mirrors).
    architecture_metadata = {
        "agent_id": "architecture",
        "version": "1.0",
        "impl": "ai_sdlc.agents.architecture.architecture_agent.ArchitectureAgent",
        "input_schema": "architecture-input-v1",
        "output_schema": "architecture-output-v1",
        "capabilities": ["reasoning"],
        "state_artifact": "architecture.json",
    }
    (agents_dir / "architecture.json").write_text(json.dumps(architecture_metadata), encoding="utf-8")
    ux_metadata = {
        "agent_id": "ux",
        "version": "1.0",
        "impl": "ai_sdlc.agents.ux.ux_agent.UXAgent",
        "input_schema": "ux-input-v1",
        "output_schema": "ux-output-v1",
        "capabilities": ["reasoning", "design"],
        "state_artifact": "ux.json",
    }
    (agents_dir / "ux.json").write_text(json.dumps(ux_metadata), encoding="utf-8")
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


def _raw_http_post(server, path: str, header_lines: str, body: bytes) -> tuple[int, bytes]:
    """Send a hand-crafted HTTP request so we can set a malformed header
    that http.client would refuse to send as-is."""
    host, port = server.server_address[0], server.server_address[1]
    with socket.create_connection((host, port), timeout=5) as sock:
        request = (
            f"POST {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            f"{header_lines}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode("utf-8") + body
        sock.sendall(request)
        sock.settimeout(5)
        response = b""
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
        except socket.timeout:
            pass
    assert response, "server closed the connection without sending any HTTP response"
    status_line = response.split(b"\r\n", 1)[0].decode("utf-8")
    status_code = int(status_line.split(" ")[1])
    return status_code, response


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


def test_state_store_transaction_prevents_lost_updates(tmp_path: Path):
    """Deterministic proof that transaction() makes a compound
    read-modify-write atomic: without it, concurrent increments would race
    and lose updates; with it, N increments always yield exactly N."""
    workspace = tmp_path / "repo"
    workspace.mkdir()
    store = StateStore(workspace)
    wf = WorkflowState(workflow_id="wf-race", current_stage="requirements")
    store.write_workflow(wf)

    def bump(_):
        with store.transaction():
            current = store.read_workflow()
            current.retry_count["counter"] = current.retry_count.get("counter", 0) + 1
            store.write_workflow(current)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(bump, range(50)))

    final = store.read_workflow()
    assert final.retry_count["counter"] == 50


def test_http_api_concurrent_approval_and_cancel_do_not_corrupt_state(tmp_path: Path):
    workspace = prepare_workspace_with_po(tmp_path)
    store = StateStore(workspace)
    wf = WorkflowState(
        workflow_id="wf-race-http",
        current_stage="requirements",
        initiator_id="u9",
        status="waiting_for_approval",
        pending_approval={"approval_id": "approval-race", "stage": "requirements", "artifact": {}, "inputs": {}},
    )
    store.write_workflow(wf)

    server, thread = _start_server(workspace)
    results = {}
    barrier = threading.Barrier(2)

    def do_approve():
        barrier.wait(timeout=5)
        results["approve"] = _http_request(server, "POST", "/v1/workflows/wf-race-http/approvals", {
            "initiator_id": "u9",
            "approval_id": "approval-race",
            "approved": True,
            "feedback": "looks good",
        })

    def do_cancel():
        barrier.wait(timeout=5)
        results["cancel"] = _http_request(server, "POST", "/v1/workflows/wf-race-http/cancel", {
            "initiator_id": "u9",
            "reason": "changed my mind",
        })

    try:
        t1 = threading.Thread(target=do_approve)
        t2 = threading.Thread(target=do_cancel)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert "approve" in results and "cancel" in results
        approve_status, approve_body = results["approve"]
        cancel_status, cancel_body = results["cancel"]

        # Neither request may crash the server or return malformed output.
        assert approve_status in (200, 409, 500, 503)
        assert cancel_status in (200, 409, 500, 503)

        # workflow.json must remain readable and valid — not torn/corrupted
        # by the race.
        final = store.read_workflow()
        assert final is not None
        assert final.status in ("cancelled", "running", "completed", "waiting_for_approval")

        # The lock must serialize the two compound operations: exactly one
        # of the racing requests actually changes workflow state, the other
        # is cleanly rejected (never both applied, never both silently lost).
        successes = [r for r in (approve_body, cancel_body) if r.get("success")]
        assert len(successes) == 1
    finally:
        server.shutdown()
        thread.join(timeout=5)


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
            # Genuinely ambiguous (contains explicit vagueness markers) so
            # the real POAgent's ambiguity heuristic asks for clarification,
            # rather than relying on stub keyword-matching.
            "raw_requirement": "TBD, not sure yet, figure out later.",
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
        pending_clarification={"question_id": "q-clarify", "stage": "requirements", "question": "Please confirm requirements", "inputs": {}},
    )
    store.write_workflow(wf)

    server, thread = _start_server(workspace)
    try:
        status, body = _http_request(server, "POST", "/v1/workflows/wf-clarify/clarifications", {
            "initiator_id": "u2",
            "question_id": "q-clarify",
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
        # In the real flow this would already be populated by start_workflow
        # (see OrchestratorAPI.start_workflow persisting raw_requirement onto
        # wf.inputs); the resume-after-approval path re-invokes the PO Agent
        # from wf.inputs with no fresh caller-supplied text, so it must
        # already be present here for the real POAgent to complete on resume.
        inputs={"requirement_text": "Add export functionality for customers."},
        pending_approval={"approval_id": "approval-approve", "stage": "requirements", "artifact": {}, "inputs": {}},
    )
    store.write_workflow(wf)

    server, thread = _start_server(workspace)
    try:
        status, body = _http_request(server, "POST", "/v1/workflows/wf-approve/approvals", {
            "initiator_id": "u3",
            "approval_id": "approval-approve",
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
            "question_id": "q-conflict",
            "response_text": "More detail please",
        })
        assert status == 409
        assert body["success"] is False
        assert body["error"]["code"] == "INVALID_STATE_TRANSITION"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_platform_http_api_cancel_success(tmp_path: Path):
    workspace = prepare_workspace_with_po(tmp_path)
    store = StateStore(workspace)
    wf = WorkflowState(workflow_id="wf-cancel-ok", current_stage="requirements", initiator_id="u1", status="running")
    store.write_workflow(wf)

    server, thread = _start_server(workspace)
    try:
        status, body = _http_request(server, "POST", "/v1/workflows/wf-cancel-ok/cancel", {
            "initiator_id": "u1",
            "reason": "no longer needed",
        })
        assert status == 200
        assert body["success"] is True
        assert body["data"]["status"] == "CANCELLED"

        status, body = _http_request(server, "GET", "/v1/workflows/wf-cancel-ok")
        assert status == 200
        assert body["data"]["status"] == "CANCELLED"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_platform_http_api_cancel_while_waiting_for_approval(tmp_path: Path):
    workspace = prepare_workspace_with_po(tmp_path)
    store = StateStore(workspace)
    wf = WorkflowState(
        workflow_id="wf-cancel-pending",
        current_stage="requirements",
        initiator_id="u2",
        status="waiting_for_approval",
        pending_approval={"approval_id": "approval-pending", "stage": "requirements", "artifact": {}, "inputs": {}},
    )
    store.write_workflow(wf)

    server, thread = _start_server(workspace)
    try:
        status, body = _http_request(server, "POST", "/v1/workflows/wf-cancel-pending/cancel", {
            "initiator_id": "u2",
            "reason": "abandoning this one",
        })
        assert status == 200
        assert body["success"] is True
        assert body["data"]["status"] == "CANCELLED"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_platform_http_api_cancel_already_terminal_is_conflict(tmp_path: Path):
    workspace = prepare_workspace_with_po(tmp_path)
    store = StateStore(workspace)
    wf = WorkflowState(workflow_id="wf-cancel-twice", current_stage="requirements", initiator_id="u3", status="cancelled")
    store.write_workflow(wf)

    server, thread = _start_server(workspace)
    try:
        status, body = _http_request(server, "POST", "/v1/workflows/wf-cancel-twice/cancel", {
            "initiator_id": "u3",
            "reason": "again",
        })
        assert status == 409
        assert body["success"] is False
        assert body["error"]["code"] == "INVALID_STATE_TRANSITION"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_platform_http_api_cancel_unauthorized_initiator(tmp_path: Path):
    workspace = prepare_workspace_with_po(tmp_path)
    store = StateStore(workspace)
    wf = WorkflowState(workflow_id="wf-cancel-unauth", current_stage="requirements", initiator_id="owner", status="running")
    store.write_workflow(wf)

    server, thread = _start_server(workspace)
    try:
        status, body = _http_request(server, "POST", "/v1/workflows/wf-cancel-unauth/cancel", {
            "initiator_id": "someone-else",
            "reason": "not mine to cancel",
        })
        assert status == 403
        assert body["success"] is False
        assert body["error"]["code"] == "UNAUTHORIZED_INITIATOR"

        # workflow must be untouched by the rejected attempt
        still_running = store.read_workflow()
        assert still_running.status == "running"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_platform_http_api_cancel_reports_not_found(tmp_path: Path):
    workspace = prepare_workspace_with_po(tmp_path)
    server, thread = _start_server(workspace)
    try:
        status, body = _http_request(server, "POST", "/v1/workflows/does-not-exist/cancel", {
            "initiator_id": "u1",
            "reason": "n/a",
        })
        assert status == 404
        assert body["success"] is False
        assert body["error"]["code"] == "WORKFLOW_NOT_FOUND"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_platform_http_api_submit_clarification_wrong_question_id(tmp_path: Path):
    workspace = prepare_workspace_with_po(tmp_path)
    store = StateStore(workspace)
    wf = WorkflowState(
        workflow_id="wf-clar-wrong",
        current_stage="requirements",
        initiator_id="u5",
        status="paused",
        pending_clarification={"question_id": "q-real", "stage": "requirements", "question": "Which fields?", "inputs": {}},
    )
    store.write_workflow(wf)

    server, thread = _start_server(workspace)
    try:
        status, body = _http_request(server, "POST", "/v1/workflows/wf-clar-wrong/clarifications", {
            "initiator_id": "u5",
            "question_id": "q-not-the-real-one",
            "response_text": "Yes use CSV",
        })
        assert status in (400, 409)
        assert body["success"] is False

        # workflow must still be paused, waiting on the real question id
        untouched = store.read_workflow()
        assert untouched.status == "paused"
        assert untouched.pending_clarification["question_id"] == "q-real"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_platform_http_api_submit_clarification_stale_id_after_consumed(tmp_path: Path):
    workspace = prepare_workspace_with_po(tmp_path)
    store = StateStore(workspace)
    wf = WorkflowState(
        workflow_id="wf-clar-stale",
        current_stage="requirements",
        initiator_id="u6",
        status="paused",
        pending_clarification={"question_id": "q-once", "stage": "requirements", "question": "Which fields?", "inputs": {}},
    )
    store.write_workflow(wf)

    server, thread = _start_server(workspace)
    try:
        status, body = _http_request(server, "POST", "/v1/workflows/wf-clar-stale/clarifications", {
            "initiator_id": "u6",
            "question_id": "q-once",
            "response_text": "Yes use CSV",
        })
        assert status == 200
        assert body["success"] is True

        # re-submitting the now-consumed id must be rejected, not re-applied
        status, body = _http_request(server, "POST", "/v1/workflows/wf-clar-stale/clarifications", {
            "initiator_id": "u6",
            "question_id": "q-once",
            "response_text": "again",
        })
        assert status in (400, 409)
        assert body["success"] is False
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_platform_http_api_submit_approval_wrong_approval_id(tmp_path: Path):
    workspace = prepare_workspace_with_po(tmp_path)
    store = StateStore(workspace)
    wf = WorkflowState(
        workflow_id="wf-appr-wrong",
        current_stage="requirements",
        initiator_id="u7",
        status="waiting_for_approval",
        pending_approval={"approval_id": "approval-real", "stage": "requirements", "artifact": {}, "inputs": {}},
    )
    store.write_workflow(wf)

    server, thread = _start_server(workspace)
    try:
        status, body = _http_request(server, "POST", "/v1/workflows/wf-appr-wrong/approvals", {
            "initiator_id": "u7",
            "approval_id": "approval-not-the-real-one",
            "approved": True,
        })
        assert status in (400, 409)
        assert body["success"] is False

        untouched = store.read_workflow()
        assert untouched.status == "waiting_for_approval"
        assert untouched.pending_approval["approval_id"] == "approval-real"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_platform_http_api_submit_approval_rejected_reports_revision_required(tmp_path: Path):
    workspace = prepare_workspace_with_po(tmp_path)
    store = StateStore(workspace)
    wf = WorkflowState(
        workflow_id="wf-appr-reject",
        current_stage="requirements",
        initiator_id="u8",
        status="waiting_for_approval",
        pending_approval={"approval_id": "approval-reject", "stage": "requirements", "artifact": {}, "inputs": {}},
    )
    store.write_workflow(wf)

    server, thread = _start_server(workspace)
    try:
        status, body = _http_request(server, "POST", "/v1/workflows/wf-appr-reject/approvals", {
            "initiator_id": "u8",
            "approval_id": "approval-reject",
            "approved": False,
            "feedback": "needs more detail",
        })
        assert status == 200
        assert body["success"] is True
        assert body["data"]["status"] == "REVISION_REQUIRED"

        # the rejection must be visible on subsequent status polls too, not
        # just in the submit response.
        status, body = _http_request(server, "GET", "/v1/workflows/wf-appr-reject")
        assert status == 200
        assert body["data"]["status"] == "REVISION_REQUIRED"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_platform_http_api_two_workflows_coexist(tmp_path: Path):
    workspace = prepare_workspace_with_po(tmp_path)
    server, thread = _start_server(workspace)
    try:
        status, body_a = _http_request(server, "POST", "/v1/workflows", {
            "initiator_id": "alice",
            "raw_requirement": "Add export functionality for customers.",
            "project_context": {},
        })
        assert status == 200
        wf_a_id = body_a["data"]["workflow_id"]

        # Starting a second workflow in the same workspace must not clobber
        # the first — each remains independently reachable by its own id.
        status, body_b = _http_request(server, "POST", "/v1/workflows", {
            "initiator_id": "bob",
            "raw_requirement": "Add import functionality for vendors.",
            "project_context": {},
        })
        assert status == 200
        wf_b_id = body_b["data"]["workflow_id"]
        assert wf_a_id != wf_b_id

        status, get_a = _http_request(server, "GET", f"/v1/workflows/{wf_a_id}")
        assert status == 200
        assert get_a["data"]["workflow_id"] == wf_a_id
        assert get_a["data"]["initiator_id"] == "alice"

        status, get_b = _http_request(server, "GET", f"/v1/workflows/{wf_b_id}")
        assert status == 200
        assert get_b["data"]["workflow_id"] == wf_b_id
        assert get_b["data"]["initiator_id"] == "bob"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_platform_http_api_workflow_id_in_body_does_not_crash(tmp_path: Path):
    workspace = prepare_workspace_with_po(tmp_path)
    store = StateStore(workspace)
    wf = WorkflowState(
        workflow_id="wf-body-id",
        current_stage="requirements",
        initiator_id="u9",
        status="paused",
        pending_clarification={"question_id": "q-body", "stage": "requirements", "question": "which fields?", "inputs": {}},
    )
    store.write_workflow(wf)

    server, thread = _start_server(workspace)
    try:
        # workflow_id is a required field on the schema, so a spec-compliant
        # client including it in the body (in addition to the URL) must not
        # crash the handler.
        status, body = _http_request(server, "POST", "/v1/workflows/wf-body-id/clarifications", {
            "workflow_id": "wf-body-id",
            "initiator_id": "u9",
            "question_id": "q-body",
            "response_text": "Yes use CSV",
        })
        assert status == 200
        assert body["success"] is True
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_platform_http_api_malformed_content_length_returns_clean_400(tmp_path: Path):
    workspace = prepare_workspace_with_po(tmp_path)
    server, thread = _start_server(workspace)
    try:
        status_code, raw = _raw_http_post(
            server,
            "/v1/workflows",
            "Content-Type: application/json\r\nContent-Length: not-a-number",
            b'{"initiator_id": "u1", "raw_requirement": "test requirement text"}',
        )
        assert status_code == 400
        assert b"VALIDATION_ERROR" in raw
    finally:
        server.shutdown()
        thread.join(timeout=5)
