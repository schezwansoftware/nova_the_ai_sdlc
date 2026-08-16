"""Sage capability abstraction: external-knowledge-connector consumption.

This is the boundary a specialist agent reaches (indirectly, via the
Orchestrator -- never directly, see below) when it realizes mid-reasoning
that it's missing information that likely already exists in one of Nova's
five shipped MCP connectors (Jira, Confluence, SharePoint, Local Docs,
OneDrive -- `packages/mcp-connectors/`). Implements the "Sage Phase 2
Knowledge Consumption Design" locked in `todo.md`/
`docs/architecture/v1_architecture.md` on 2026-08-16.

Mirrors `retrieval.py`'s structure closely -- same `ABC` +
`ProviderError`/`MalformedResponseError` failure contract, same
strict-Pydantic-request/response discipline, same "bounded agentic loop
behind a single-call interface" shape -- and, per this module's
self-containment convention (see `retrieval.py`'s docstring for why
`RetrievalCapability` doesn't import `coding.py`'s `TerminationReason`),
these are defined fresh here rather than imported.

## Why this is a separate, new capability -- not `ReasoningCapability` with
## tools attached

`ReasoningCapability.complete()` is **structurally zero-tool by
deliberate design** (`reasoning_anthropic.py` forces a single
`tool_choice`; `reasoning_copilot.py` passes `available_tools=[]`) -- an
explicit anti-prompt-injection guarantee. Giving PO/Architecture/UX
agents live tool access through that capability would break it. Sage is
therefore a genuinely separate capability: a calling agent never touches
connectors directly. It sets `needs_context`/`context_query` on its own
structured output (see `agents/*/schemas.py`); the Orchestrator
(`orchestration/orchestrator.py`) is the only caller of `SageCapability`,
and it invokes Sage's own separate, isolated, bounded agentic sub-session
-- MCP tools attached *only inside that sub-session*. Only the final
distilled, cited `SageResponse` ever crosses back into a worker's prompt
(see `agents/*/prompts.py`'s `sage_context` rendering) -- never the raw
tool-call transcript.

## Relationship to `RetrievalCapability`

Different capability, different scope, never overlapping:
`RetrievalCapability` is Tier 2 **codebase** grounding (used by
Architecture via `_gather_codebase_context()`); `SageCapability` is
grounding against the 5 **external** knowledge connectors. Both happen to
use a similar "harnessed agentic session behind a single-call interface"
shape because that shape is simply the right tool for "bounded, tool-
using exploration in service of one answer" regardless of what's being
explored -- not because one is built on the other.

## Local memory: owned by Sage, not a new agent

Per the locked design, "keep a running memory of what's been learned"
belongs to Sage (already owns "Knowledge/RAG... context engineering" per
the ownership table), not a new team member. `SageMemoryEntry`/
`normalize_context_query` live here (co-located with the capability they
serve) even though the actual storage/lookup is done by
`orchestration/state.py::StateStore` (`read_sage_memory`/
`write_sage_memory_entry`) and the read-before-ask/write-after-answer
decision is made by `Orchestrator.invoke_agent_for_stage`, not by any
`SageCapability` provider itself -- providers only ever answer a single
question; they have no persistence responsibility.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator

#: Bounded-agentic-loop step budget for a single Sage question. Smaller
#: than `retrieval.py`'s `DEFAULT_MAX_STEPS = 20`: every connector exposes
#: exactly 2 tools (search/fetch, per `packages/mcp-connectors`), so a
#: Sage session converges faster than open-ended codebase exploration.
DEFAULT_MAX_STEPS = 12


class ProviderError(Exception):
    """Raised when the underlying Sage provider fails to produce a result
    at all (e.g. the harnessed agentic-tool subprocess/session could not
    be started, crashed, or timed out before returning any usable
    result). Callers should generally treat this as a retryable
    condition -- though see `orchestration/orchestrator.py`'s
    NEEDS_CONTEXT handling: a Sage failure never fails the *workflow*,
    it's treated the same as Sage finding nothing."""


class MalformedResponseError(Exception):
    """Raised when the underlying Sage provider *did* run its exploration
    to some conclusion, but the outcome could not be parsed/validated into
    a `SageResponse`."""


class TerminationReason(str, Enum):
    """Why a provider's bounded exploration loop stopped. Mirrors
    `retrieval.py`'s `TerminationReason` vocabulary exactly (kept as a
    separate definition per this module's self-containment convention)."""

    COMPLETED = "completed"
    STEP_BUDGET_EXHAUSTED = "step_budget_exhausted"
    PROVIDER_REPORTED_FAILURE = "provider_reported_failure"


def _nonempty_str(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise ValueError("must not be empty")
    return value


class SageRequest(BaseModel):
    """Input payload to `SageCapability.ask()`: a worker's plain-language
    context question."""

    query: str
    #: Observability/audit only -- Sage never branches on this (the
    #: locked design's edge case #4: "connector routing stays entirely
    #: Sage's own judgment call every time, never hinted at by the
    #: caller").
    requesting_agent_id: str
    #: Per-call override of `DEFAULT_MAX_STEPS`. `None` means "use the
    #: provider's platform-wide default".
    max_steps: Optional[int] = None

    @field_validator("query", "requesting_agent_id")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        return _nonempty_str(value)

    @field_validator("max_steps")
    @classmethod
    def _max_steps_positive(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value <= 0:
            raise ValueError("max_steps must be a positive integer when set")
        return value


class SageResponse(BaseModel):
    """Provider response envelope returned by `SageCapability.ask()`.

    `found=False` is a normal, valid outcome, not an error -- per the
    locked design's edge case #2 ("Sage finds nothing anywhere... reported
    back as a normal, valid 'nothing found' result"). `answer`/
    `source_connector`/`source_url` are empty/`None` when `found=False`.
    """

    query: str
    found: bool
    answer: str = ""
    source_connector: Optional[str] = None
    source_url: Optional[str] = None
    provider_name: str
    #: How many bounded-loop steps the provider actually used, for
    #: observability/cost tracking against `max_steps`/`DEFAULT_MAX_STEPS`.
    steps_used: int
    terminated_reason: TerminationReason
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("query", "provider_name")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        return _nonempty_str(value)

    @field_validator("steps_used")
    @classmethod
    def _steps_used_nonnegative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("steps_used must not be negative")
        return value


class SageCapability(ABC):
    """Abstract Sage capability.

    The Orchestrator calls `ask(request)` and receives back a validated
    `SageResponse` -- a distilled, cited answer (or a normal "not found"
    result) for `request.query`. Implementations are responsible for
    whatever connector search/fetch exploration is needed to satisfy
    `SageResponse` within `request.max_steps` (or `DEFAULT_MAX_STEPS`),
    and must raise `ProviderError` or `MalformedResponseError` (never an
    arbitrary/unrelated exception) on failure -- mirroring every other
    capability in this package's failure contract.
    """

    @abstractmethod
    def ask(self, request: SageRequest) -> SageResponse:
        """Run a bounded, tool-using exploration across this workspace's
        enabled MCP connectors and return a validated `SageResponse`.

        Raises:
            ProviderError: the provider could not produce a result at all.
            MalformedResponseError: the provider ran, but the outcome does
                not satisfy `SageResponse`.
        """
        raise NotImplementedError()


class SageMemoryEntry(BaseModel):
    """A single cached, previously-Sage-answered context entry. Only
    `found=True` answers are ever written (see
    `orchestration/orchestrator.py`) -- a miss is never cached, since
    caching "nothing was found" would prevent a later, differently-
    configured connector set from ever being tried again for the same
    query.

    Carries `source`/`saved_at` alongside the answer per the locked
    design ("a fact without provenance can't later be judged
    trustworthy"); nothing expires entries automatically -- `saved_at` is
    exposed as data for a worker's own prompt/reasoning to weigh ("may be
    incomplete or stale"), not an enforced TTL rule (see `todo.md`'s
    deliberately-deferred list).
    """

    query: str
    answer: str
    found: bool
    source_connector: Optional[str] = None
    source_url: Optional[str] = None
    saved_at: str


def normalize_context_query(query: str) -> str:
    """Cheap, plain lookup key for Sage's local memory -- exact-match on
    normalized text, deliberately not fuzzy/semantic, per the locked
    design's explicit "cheap plain lookup, not a search index"
    requirement. Collapses whitespace and lowercases; two differently-
    phrased-but-equivalent queries will NOT share a cache hit -- an
    accepted limitation, not a bug (see `todo.md`'s deliberately-deferred
    list)."""
    return " ".join((query or "").strip().lower().split())
