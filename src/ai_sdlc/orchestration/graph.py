from __future__ import annotations
from typing import Callable, Dict, List, Optional
from enum import Enum
from dataclasses import dataclass


class NodeType(str, Enum):
    ACTION = "action"
    APPROVAL = "approval"
    HUMAN_INPUT = "human_input"


@dataclass
class Node:
    node_id: str
    node_type: NodeType
    handler: Optional[Callable] = None


@dataclass
class Transition:
    from_node: str
    to_node: str
    condition: Optional[Callable[[Dict], bool]] = None


class GraphRunner:
    """Lightweight graph runner abstraction.

    This is intentionally small: it provides a node/transition model and a
    simple execution loop. The implementation is designed so a LangGraph
    integration can be swapped in later by replacing GraphRunner's internals.
    """

    def __init__(self):
        self.nodes: Dict[str, Node] = {}
        self.transitions: List[Transition] = []

    def add_node(self, node: Node):
        self.nodes[node.node_id] = node

    def add_transition(self, t: Transition):
        self.transitions.append(t)

    def run(self, start_node: str, context: Dict):
        current = start_node
        visited = []
        while current:
            visited.append(current)
            node = self.nodes.get(current)
            if not node:
                raise RuntimeError(f"Unknown node: {current}")

            # ACTION nodes execute handler and expect it to update context
            if node.handler:
                result = node.handler(context)
                # Handler may return control signals
                if isinstance(result, dict) and result.get("interrupt"):
                    return {"status": "interrupted", "node": current, "context": context}

            # Find the first matching transition
            next_node = None
            for t in self.transitions:
                if t.from_node != current:
                    continue
                if t.condition is None or t.condition(context):
                    next_node = t.to_node
                    break

            if not next_node:
                return {"status": "completed", "visited": visited, "context": context}

            current = next_node

        return {"status": "completed", "visited": visited, "context": context}


# Note: This module intentionally does not import LangGraph directly so unit
# tests and early development can proceed without an external dependency.
# When integrating LangGraph, implement a subclass or adapter that exposes the
# same GraphRunner API and delegates execution to LangGraph nodes/transitions.
