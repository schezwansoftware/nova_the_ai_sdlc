from __future__ import annotations
import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Dict, Any, Type
from urllib.parse import urlparse

from pydantic import ValidationError

from ai_sdlc.platform.core import CorePlatform
from ai_sdlc.orchestration.api import (
    APIErrorDetail,
    APIResponse,
    CancelWorkflowRequest,
    ErrorCode,
    GetWorkflowStatusRequest,
    ResumeWorkflowRequest,
    StartWorkflowRequest,
    SubmitApprovalRequest,
    SubmitClarificationRequest,
)


def _json_response(response: APIResponse) -> bytes:
    return json.dumps(response.model_dump(), default=str).encode("utf-8")


class PlatformHTTPRequestHandler(BaseHTTPRequestHandler):
    backend: CorePlatform

    def _response_status_code(self, response: APIResponse) -> int:
        if getattr(response, "success", False):
            return 200

        error = getattr(response, "error", None)
        code = None
        if error is not None:
            if hasattr(error, "code"):
                code = error.code
            elif isinstance(error, dict):
                code = error.get("code")

        if code == ErrorCode.VALIDATION_ERROR:
            return 400
        if code == ErrorCode.WORKFLOW_NOT_FOUND:
            return 404
        if code == ErrorCode.UNAUTHORIZED_INITIATOR:
            return 403
        if code == ErrorCode.INVALID_STATE_TRANSITION:
            return 409
        if code == ErrorCode.LOCK_ACQUISITION_FAILED:
            return 503
        return 500

    def _send_json(self, response: APIResponse, status_code: int | None = None) -> None:
        payload = _json_response(response)
        resolved_status = self._response_status_code(response) if status_code is None else status_code
        self.send_response(resolved_status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        raw_body = self.rfile.read(length)
        return json.loads(raw_body.decode("utf-8"))

    def _handle_request(self, handler):
        try:
            body = self._read_body()
        except json.JSONDecodeError:
            self._send_json(
                APIResponse(
                    success=False,
                    error=APIErrorDetail(code=ErrorCode.VALIDATION_ERROR, message="Invalid JSON payload"),
                ),
                status_code=400,
            )
            return

        try:
            response = handler(body)
            self._send_json(response)
        except ValidationError as validation_error:
            response = APIResponse(
                success=False,
                error=APIErrorDetail(code=ErrorCode.VALIDATION_ERROR, message=str(validation_error)),
            )
            self._send_json(response, status_code=400)
        except Exception as exc:
            response = APIResponse(
                success=False,
                error=APIErrorDetail(code=ErrorCode.INTERNAL_ORCHESTRATION_ERROR, message=str(exc)),
            )
            self._send_json(response, status_code=500)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/v1/workflows":
            self._handle_request(self._start_workflow)
            return

        match = re.match(r"^/v1/workflows/(?P<wid>[^/]+)/clarifications$", parsed.path)
        if match:
            self._handle_request(lambda body: self._submit_clarification(match.group("wid"), body))
            return

        match = re.match(r"^/v1/workflows/(?P<wid>[^/]+)/approvals$", parsed.path)
        if match:
            self._handle_request(lambda body: self._submit_approval(match.group("wid"), body))
            return

        match = re.match(r"^/v1/workflows/(?P<wid>[^/]+)/resume$", parsed.path)
        if match:
            self._handle_request(lambda body: self._resume_workflow(match.group("wid"), body))
            return

        match = re.match(r"^/v1/workflows/(?P<wid>[^/]+)/cancel$", parsed.path)
        if match:
            self._handle_request(lambda body: self._cancel_workflow(match.group("wid"), body))
            return

        self.send_error(404, "Endpoint not found")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        match = re.match(r"^/v1/workflows/(?P<wid>[^/]+)$", parsed.path)
        if match:
            response = self._get_workflow_status(match.group("wid"))
            self._send_json(response)
            return

        self.send_error(404, "Endpoint not found")

    def _start_workflow(self, body: Dict[str, Any]) -> APIResponse:
        request = StartWorkflowRequest(**body)
        return self.backend.start_workflow(request)

    def _get_workflow_status(self, workflow_id: str) -> APIResponse:
        request = GetWorkflowStatusRequest(workflow_id=workflow_id)
        return self.backend.get_workflow_status(request)

    def _submit_clarification(self, workflow_id: str, body: Dict[str, Any]) -> APIResponse:
        request = SubmitClarificationRequest(workflow_id=workflow_id, **body)
        return self.backend.submit_clarification(request)

    def _submit_approval(self, workflow_id: str, body: Dict[str, Any]) -> APIResponse:
        request = SubmitApprovalRequest(workflow_id=workflow_id, **body)
        return self.backend.submit_approval(request)

    def _resume_workflow(self, workflow_id: str, body: Dict[str, Any]) -> APIResponse:
        request = ResumeWorkflowRequest(workflow_id=workflow_id, **body)
        return self.backend.resume_workflow(request)

    def _cancel_workflow(self, workflow_id: str, body: Dict[str, Any]) -> APIResponse:
        request = CancelWorkflowRequest(workflow_id=workflow_id, **body)
        return self.backend.cancel_workflow(request)

    def log_message(self, format: str, *args: Any) -> None:
        return


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def make_platform_handler(workspace: str) -> Type[PlatformHTTPRequestHandler]:
    class Handler(PlatformHTTPRequestHandler):
        backend = CorePlatform(workspace)

    return Handler


def run_platform_server(workspace: str, host: str = "127.0.0.1", port: int = 8000) -> HTTPServer:
    handler_class = make_platform_handler(workspace)
    server = ThreadedHTTPServer((host, port), handler_class)
    return server


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run AI SDLC Core Platform HTTP API server.")
    parser.add_argument("workspace", help="Path to the workspace root")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    server = run_platform_server(args.workspace, host=args.host, port=args.port)
    print(f"Serving Core Platform HTTP API on http://{args.host}:{args.port}")
    server.serve_forever()
