from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, Optional
import json
import importlib


class AgentRegistry:
    """Agent Registry that discovers agent metadata under workspace/.ai-sdlc/agents/

    Each JSON metadata file should include at least:
    {
      "agent_id": "po",
      "version": "1.0",
      "impl": "ai_sdlc.agents.po.po_agent.POAgent",
      "input_schema": "po-input-v1",
      "output_schema": "po-output-v1",
      "capabilities": ["reasoning"],
      "state_artifact": "requirements.json"
    }

    The registry will attempt to import the implementation path and instantiate
    the class with no arguments. Discovered agent objects are available via get(agent_id).
    """

    def __init__(self, workspace: Optional[Path] = None):
        self.workspace = Path(workspace) if workspace else None
        self.agents: Dict[str, Any] = {}
        # store metadata for discovery
        self.metadata: Dict[str, Dict] = {}
        if self.workspace:
            self.discover()

    def discover(self) -> None:
        if not self.workspace:
            return
        agents_dir = self.workspace / ".ai-sdlc" / "agents"
        if not agents_dir.exists():
            return
        for p in agents_dir.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                aid = data.get("agent_id")
                if not aid:
                    continue
                self.metadata[aid] = data
                impl = data.get("impl")
                if impl:
                    obj = self._load_impl(impl)
                    if obj:
                        self.agents[aid] = obj
            except Exception:
                # discovery should not raise; skip invalid files
                continue

    def _load_impl(self, impl_path: str):
        try:
            module_path, class_name = impl_path.rsplit('.', 1)
            module = importlib.import_module(module_path)
            cls = getattr(module, class_name)
            return cls()
        except Exception:
            return None

    def register(self, agent_id: str, agent_obj: Any) -> None:
        self.agents[agent_id] = agent_obj

    def get(self, agent_id: str) -> Optional[Any]:
        return self.agents.get(agent_id)

    def get_metadata(self, agent_id: str) -> Optional[Dict]:
        return self.metadata.get(agent_id)
