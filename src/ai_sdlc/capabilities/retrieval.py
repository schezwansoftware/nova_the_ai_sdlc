"""Retrieval capability abstraction.

This is the boundary Tier 2 agents (Architecture, Review, Documentation --
`docs/architecture/v1_architecture.md` section 8's Agent Capability Tiers)
call through instead of depending on a specific search/RAG/agentic-tool
SDK for codebase grounding. Concrete providers implement
`RetrievalCapability.retrieve()`; agents only ever see this interface.
Mirrors `capabilities/coding.py`'s structure closely -- same `ABC` +
`ProviderError`/`MalformedResponseError` failure contract, same strict-
Pydantic-request/response discipline, same "bounded agentic loop behind a
single-call interface" shape -- because section 8's addendum (see the
paragraph right before "Agent Capability Tiers") explicitly says V1's
`RetrievalCapability` codebase-grounding provider *reuses*
`CodingCapability`'s already-built harnessing pattern rather than
re-solving it, permissioned read-only.

`ProviderError`, `MalformedResponseError`, and the local
`TerminationReason` enum are deliberately **not** imported from
`coding.py` even though they're conceptually identical -- this follows
the same self-containment convention `capabilities/__init__.py` already
documents for why `ReasoningCapability`/`DesignCapability` each define
their own failure-contract classes instead of sharing one: importing
across capability modules would couple this capability's stability to
`CodingCapability`'s internals for no real benefit, and duplicating three
small, stable definitions is cheaper than that coupling.

Conceptually:

    Architecture / Review / Documentation Agent (Tier 2, Craft)
            |
            v
    RetrievalCapability   (this module)
            |
            v
    Configured provider (`providers/retrieval_mock.py` for V1/tests; the
    real read-only Claude Agent SDK provider -- see
    `providers/retrieval_claude.py` -- for the default V1 runtime
    provider; Sage's originally-scoped dual-index design remains a
    documented future/scale-path provider behind this same seam, per
    section 9's "Future/Scale Path" and section 18 Decision 6, not built
    now)

Request/response shape (Forge's derived design):
`docs/architecture/v1_architecture.md` section 9 describes the conceptual
contract -- "query strings, semantic context scope" in (section 3's
Knowledge Engine row), "a tight token-budgeted Context Pack" /
"metadata-attributed code snippets" out -- but not a literal schema.
`RetrievalRequest`/`RetrievalResult` below are Forge's derived schemas
carrying that same substance in a provider-agnostic shape. In particular:

  - `RetrievalRequest.repository_path` points directly at the **real**
    target repository, not an isolated worktree copy the way
    `CodingRequest.working_tree_path` does. This is a deliberate,
    reasoned difference, not an oversight: `CodingCapability` needs
    isolation because its provider is *granted* edit/write/execute tools,
    and isolation is what keeps a mistake from touching the initiator's
    live checkout before human approval (section 4/10). `Retrieval
    Capability`'s provider is never granted any write/edit/command-
    execution tool at all -- the tool *definitions* themselves are
    removed from what the underlying agent can even attempt (see
    `providers/retrieval_claude.py`'s docstring for exactly how). A
    provider that structurally cannot mutate anything has nothing for
    isolation to protect against; requiring a disposable worktree here
    would add a setup/teardown lifecycle this capability has no use for,
    for a threat model (accidental/malicious repository mutation) that's
    already closed off at the tool-permission level, matching section
    18 Decision 5's "structurally incapable... rather than merely
    trusted not to" principle. Nothing prevents a caller from pointing
    this at an isolated worktree anyway (e.g. one already checked out
    mid-`CodingCapability` flow) -- the field just never requires it.
  - `RetrievalRequest.scope_paths` is the "semantic context scope" input
    section 3's Knowledge Engine row describes -- an optional hint
    narrowing where the provider should look first, not a hard filter it
    must enforce (a harnessed exploratory agent may still follow a
    reference outside the hinted scope if that's where the answer
    actually lives).
  - `RetrievalResult.context_summary` is the "tight token-budgeted
    Context Pack" section 9's "Context Injection" bullet describes;
    `RetrievalRequest.max_context_tokens` is the caller's budget for it,
    enforced as a best-effort prompt instruction plus a coarse
    characters-per-token safety-net truncation (see
    `providers/retrieval_claude.py`) -- not exact token counting, since
    that needs a real tokenizer this module deliberately doesn't take a
    hard dependency on.
  - `RetrievalResult.snippets` is the "metadata-attributed code snippets"
    section 3 describes -- structured, sourced fragments the provider
    drew its summary from, distinct from the synthesized prose in
    `context_summary`. Empty is valid: some queries resolve to a pure
    synthesized answer with nothing worth quoting verbatim.
  - `RetrievalRequest.max_steps` mirrors `CodingRequest.max_steps`
    exactly (a per-call override of `DEFAULT_MAX_STEPS`, itself smaller
    than `coding.py`'s equivalent constant -- see that constant for why).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

#: Default bounded-agentic-loop step budget for retrieval, mirroring
#: `coding.py`'s `DEFAULT_MAX_STEPS` in spirit but sized smaller: a
#: read-only explore-then-summarize task (find and read a handful of
#: relevant files, follow a reference or two) converges faster and
#: cheaper than a full read/edit/run-command/react coding loop, so a
#: platform-wide default of 20 comfortably covers typical grounding
#: queries without letting a provider that can't converge -- e.g. a
#: query too vague to resolve to any relevant file -- run indefinitely.
#: Callers needing a different ceiling set `RetrievalRequest.max_steps`
#: explicitly; this constant is only the fallback when they don't.
DEFAULT_MAX_STEPS = 20

#: Soft token budget for `RetrievalResult.context_summary` when the
#: caller doesn't specify `RetrievalRequest.max_context_tokens` -- see
#: module docstring for how this is enforced (best-effort, not exact).
DEFAULT_MAX_CONTEXT_TOKENS = 4000


class ProviderError(Exception):
    """Raised when the underlying retrieval provider fails to produce a
    result at all (e.g. the harnessed agentic-tool subprocess/session
    could not be started, crashed, or timed out before returning any
    usable result). Callers should generally treat this as a retryable
    condition."""


class MalformedResponseError(Exception):
    """Raised when the underlying retrieval provider *did* run its
    exploration to some conclusion, but the outcome could not be parsed/
    validated into a `RetrievalResult`."""


class TerminationReason(str, Enum):
    """Why a provider's bounded exploration loop stopped. Mirrors
    `coding.py`'s `TerminationReason` vocabulary exactly (kept as a
    separate definition per this module's self-containment convention --
    see module docstring)."""

    COMPLETED = "completed"
    STEP_BUDGET_EXHAUSTED = "step_budget_exhausted"
    PROVIDER_REPORTED_FAILURE = "provider_reported_failure"


def _nonempty_str(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise ValueError("must not be empty")
    return value


class RetrievalRequest(BaseModel):
    """Input payload to `RetrievalCapability.retrieve()`: the query and
    target-repository context described in
    `docs/architecture/v1_architecture.md` section 3's Knowledge Engine
    row and section 9."""

    query: str
    #: Path to the real target repository -- see module docstring for why
    #: this is deliberately not an isolated worktree the way
    #: `CodingRequest.working_tree_path` is.
    repository_path: str
    #: Optional hint narrowing where the provider should look first (the
    #: "semantic context scope" input in section 3's Knowledge Engine
    #: row). Not a hard filter -- see module docstring.
    scope_paths: List[str] = Field(default_factory=list)
    #: Soft token budget for the returned `context_summary`. `None` means
    #: "use `DEFAULT_MAX_CONTEXT_TOKENS`".
    max_context_tokens: Optional[int] = None
    #: Per-call override of `DEFAULT_MAX_STEPS`. `None` means "use the
    #: provider's platform-wide default".
    max_steps: Optional[int] = None
    #: Opaque, provider-specific hints the capability/provider may
    #: interpret; the calling agent never inspects vendor-specific keys
    #: itself (mirrors `CodingRequest.provider_policy`).
    provider_policy: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("query", "repository_path")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        return _nonempty_str(value)

    @field_validator("max_context_tokens")
    @classmethod
    def _max_context_tokens_positive(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value <= 0:
            raise ValueError("max_context_tokens must be a positive integer when set")
        return value

    @field_validator("max_steps")
    @classmethod
    def _max_steps_positive(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value <= 0:
            raise ValueError("max_steps must be a positive integer when set")
        return value


class ContextSnippet(BaseModel):
    """A single sourced, metadata-attributed fragment the provider drew
    its `context_summary` from -- the "metadata-attributed code snippets"
    section 3's Knowledge Engine row describes."""

    source_path: str
    content: str
    line_start: Optional[int] = None
    line_end: Optional[int] = None

    @field_validator("source_path", "content")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        return _nonempty_str(value)


class RetrievalResult(BaseModel):
    """Provider response envelope returned by
    `RetrievalCapability.retrieve()`: the token-budgeted Context Pack
    (`context_summary` + `snippets`) plus provider response envelope, per
    `docs/architecture/v1_architecture.md` section 3/9."""

    query: str
    context_summary: str
    snippets: List[ContextSnippet] = Field(default_factory=list)
    provider_name: str
    #: How many bounded-loop steps the provider actually used, for
    #: observability/cost tracking against `max_steps`/`DEFAULT_MAX_STEPS`.
    steps_used: int
    terminated_reason: TerminationReason
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("query", "context_summary", "provider_name")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        return _nonempty_str(value)

    @field_validator("steps_used")
    @classmethod
    def _steps_used_nonnegative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("steps_used must not be negative")
        return value


class RetrievalCapability(ABC):
    """Abstract retrieval capability.

    Tier 2 agents call `retrieve(request)` and receive back a validated
    `RetrievalResult` -- a token-budgeted context summary plus sourced
    snippets -- for `request.query` against `request.repository_path`.
    Implementations are responsible for whatever exploration/search is
    needed to satisfy `RetrievalResult` within `request.max_steps` (or
    `DEFAULT_MAX_STEPS`), and must raise `ProviderError` or
    `MalformedResponseError` (never an arbitrary/unrelated exception) on
    failure -- mirroring `ReasoningCapability`/`DesignCapability`/
    `CodingCapability`'s failure contract exactly, so callers can handle
    all capabilities uniformly. Unlike `CodingCapability`, a
    `RetrievalCapability` provider must never be granted any tool capable
    of editing, writing, or executing commands against the target
    repository -- see `providers/retrieval_claude.py` for how the V1
    provider enforces that structurally, not just by convention.
    """

    @abstractmethod
    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        """Run a bounded, read-only exploration against
        `request.repository_path` and return a validated
        `RetrievalResult`.

        Raises:
            ProviderError: the provider could not produce a result at all.
            MalformedResponseError: the provider ran, but the outcome does
                not satisfy `RetrievalResult`.
        """
        raise NotImplementedError()
