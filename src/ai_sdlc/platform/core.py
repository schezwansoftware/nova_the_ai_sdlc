from __future__ import annotations
from ai_sdlc.orchestration.api import OrchestratorAPI
from ai_sdlc.orchestration.state import StateStore


class CorePlatform:
    """Platform foundation layer that exposes the stable public orchestrator API."""

    def __init__(self, workspace: str):
        self.workspace = workspace
        self.state_engine = StateStore(workspace)
        self.orchestrator_api = OrchestratorAPI(workspace)

    def start_workflow(self, request):
        return self.orchestrator_api.start_workflow(request)

    def get_workflow_status(self, request):
        return self.orchestrator_api.get_workflow_status(request)

    def submit_clarification(self, request):
        return self.orchestrator_api.submit_clarification(request)

    def submit_approval(self, request):
        return self.orchestrator_api.submit_approval(request)

    def resume_workflow(self, request):
        return self.orchestrator_api.resume_workflow(request)

    def cancel_workflow(self, request):
        return self.orchestrator_api.cancel_workflow(request)
