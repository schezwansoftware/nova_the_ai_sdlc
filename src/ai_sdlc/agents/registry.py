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
        # `agent_id` -> `str(exception)` for any metadata file whose `impl`
        # was found but failed to *construct* (as opposed to a malformed/
        # unreadable metadata file, which `discover()` still silently
        # skips -- that's a different, genuinely-ignorable failure mode).
        # A real specialist agent constructor can legitimately raise today
        # (e.g. a `ReasoningCapability`/`RetrievalCapability`/
        # `CodingCapability` provider-selection factory raising
        # `ProviderError` because a configured real provider's SDK isn't
        # installed) -- previously `_load_impl` swallowed that
        # unconditionally, so `get(agent_id)` returning `None` and a
        # misleading "Agent not found: <id>" (`orchestration/orchestrator.py`)
        # was the *only* symptom, with zero indication the agent actually
        # exists and simply failed to construct. Recording the reason here
        # lets the orchestrator surface it instead of hiding it.
        self.load_errors: Dict[str, str] = {}
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
                    obj, error = self._load_impl(impl)
                    if obj:
                        self.agents[aid] = obj
                    elif error:
                        self.load_errors[aid] = error
            except Exception:
                # discovery should not raise; skip invalid/unreadable
                # metadata files -- unlike a construction failure, there's
                # no agent_id to attribute this to yet.
                continue

    def _load_impl(self, impl_path: str):
        """Import `impl_path` and construct it with no arguments.

        Returns `(instance, None)` on success, or `(None, str(exception))`
        on failure -- the caller decides what to do with the reason
        (`discover()` records it in `self.load_errors`) rather than this
        method silently discarding it.
        """
        try:
            module_path, class_name = impl_path.rsplit('.', 1)
            module = importlib.import_module(module_path)
            cls = getattr(module, class_name)
            return cls(), None
        except Exception as exc:
            return None, str(exc)

    def register(self, agent_id: str, agent_obj: Any) -> None:
        self.agents[agent_id] = agent_obj

    def get(self, agent_id: str) -> Optional[Any]:
        return self.agents.get(agent_id)

    def get_metadata(self, agent_id: str) -> Optional[Dict]:
        return self.metadata.get(agent_id)

    def get_load_error(self, agent_id: str) -> Optional[str]:
        """The reason `agent_id`'s implementation failed to construct, if
        its metadata was found but `_load_impl` raised. `None` if the
        agent loaded successfully, or if no metadata file for it was ever
        found at all (a different failure mode -- see
        `orchestration/orchestrator.py`'s use of this)."""
        return self.load_errors.get(agent_id)
