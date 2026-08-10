"""Thin HTTP client for Core's Platform REST API
(`ai_sdlc.platform.server.PlatformHTTPRequestHandler`). This is the CLI's
only way of talking to the orchestration layer -- no orchestration module
is ever imported here."""
from __future__ import annotations

import json
import socket
from http.client import HTTPConnection
from typing import Any, Dict, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from ai_sdlc.cli.schemas import (
    CancelWorkflowData,
    CancelWorkflowRequest,
    StartWorkflowData,
    StartWorkflowRequest,
    SubmitApprovalData,
    SubmitApprovalRequest,
    SubmitClarificationData,
    SubmitClarificationRequest,
    WorkflowStatusData,
)

T = TypeVar("T", bound=BaseModel)


class APIError(Exception):
    """The server understood the request but returned `success: false`."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class ConnectionUnavailable(Exception):
    """The CLI could not reach the Core Platform API at all."""


class PlatformClient:
    def __init__(self, host: str, port: int, timeout: float = 15.0):
        self.host = host
        self.port = port
        self.timeout = timeout

    def is_reachable(self) -> bool:
        try:
            with socket.create_connection((self.host, self.port), timeout=1.5):
                return True
        except OSError:
            return False

    def _request(self, method: str, path: str, body: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        try:
            conn = HTTPConnection(self.host, self.port, timeout=self.timeout)
            payload = json.dumps(body).encode("utf-8") if body is not None else None
            headers = {"Content-Type": "application/json"} if payload is not None else {}
            conn.request(method, path, body=payload, headers=headers)
            response = conn.getresponse()
            raw = response.read().decode("utf-8")
            conn.close()
        except OSError as exc:
            raise ConnectionUnavailable(
                f"Could not reach the Core Platform API at http://{self.host}:{self.port} ({exc})."
            ) from exc

        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise APIError("INTERNAL_ORCHESTRATION_ERROR", f"Malformed response from server: {exc}") from exc

    def _call(self, method: str, path: str, body: Optional[Dict[str, Any]], data_model: Type[T]) -> T:
        envelope = self._request(method, path, body)
        if not envelope.get("success"):
            error = envelope.get("error") or {}
            raise APIError(error.get("code", "UNKNOWN"), error.get("message", "Unknown error"))
        try:
            return data_model.model_validate(envelope.get("data") or {})
        except ValidationError as exc:
            raise APIError(
                "INTERNAL_ORCHESTRATION_ERROR", f"Server response did not match the expected shape: {exc}"
            ) from exc

    def start_workflow(
        self, initiator_id: str, raw_requirement: str, project_context: Dict[str, Any]
    ) -> StartWorkflowData:
        request = StartWorkflowRequest(
            initiator_id=initiator_id, raw_requirement=raw_requirement, project_context=project_context
        )
        return self._call("POST", "/v1/workflows", request.model_dump(), StartWorkflowData)

    def get_status(self, workflow_id: str) -> WorkflowStatusData:
        return self._call("GET", f"/v1/workflows/{workflow_id}", None, WorkflowStatusData)

    def submit_clarification(
        self, workflow_id: str, initiator_id: str, question_id: str, response_text: str
    ) -> SubmitClarificationData:
        request = SubmitClarificationRequest(
            initiator_id=initiator_id, question_id=question_id, response_text=response_text
        )
        return self._call(
            "POST", f"/v1/workflows/{workflow_id}/clarifications", request.model_dump(), SubmitClarificationData
        )

    def submit_approval(
        self,
        workflow_id: str,
        initiator_id: str,
        approval_id: str,
        approved: bool,
        feedback: Optional[str] = None,
    ) -> SubmitApprovalData:
        request = SubmitApprovalRequest(
            initiator_id=initiator_id, approval_id=approval_id, approved=approved, feedback=feedback
        )
        return self._call(
            "POST", f"/v1/workflows/{workflow_id}/approvals", request.model_dump(), SubmitApprovalData
        )

    def cancel_workflow(self, workflow_id: str, initiator_id: str, reason: str) -> CancelWorkflowData:
        request = CancelWorkflowRequest(initiator_id=initiator_id, reason=reason)
        return self._call("POST", f"/v1/workflows/{workflow_id}/cancel", request.model_dump(), CancelWorkflowData)
