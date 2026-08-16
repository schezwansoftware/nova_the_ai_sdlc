"""Deterministic, offline `ReasoningCapability` implementation.

`MockReasoningProvider` makes zero network calls. It derives structured
output purely by parsing the plain-text `prompt` it is handed and filling
the requested Pydantic `output_schema` with rule-based values -- e.g.
functional requirements are derived from imperative-sounding sentences in
the input text, non-functional requirements from sentences containing
performance/security/reliability keywords, and so on.

This remains the hard default `ReasoningCapability` implementation --
every test, CI run, and any workspace that hasn't explicitly configured a
real provider gets this, never a live model call (see
`providers/reasoning_factory.py`). A real provider now also exists
(`providers/reasoning_anthropic.py`'s `AnthropicReasoningProvider`,
backed by the Anthropic Messages API), added without any agent code
changing, because agents only ever depend on `ReasoningCapability`.

Test hooks (documented, not a hidden hack):
    - `MockReasoningProvider(force_error="malformed")` makes every
      `complete()` call deliberately return a payload that fails
      `output_schema` validation, raising `MalformedResponseError`.
    - `MockReasoningProvider(force_error="provider_failure")` makes every
      `complete()` call raise `ProviderError` before generating anything,
      simulating a network/vendor outage.
    - Either can also be passed per-call via `complete(..., force_error=...)`,
      which takes precedence over the constructor-level setting for that
      one call.
    - `MockReasoningProvider(trigger_needs_context=True)` makes every
      `complete()` call against a schema that declares `needs_context`/
      `context_query` (PO/Architecture/UX -- see `agents/*/schemas.py`)
      deterministically request context instead of the normal rule-based
      derivation, so the full Orchestrator -> memory -> Sage -> resume
      loop (`orchestration/orchestrator.py`) can be exercised without a
      real LLM. Also available per-call via
      `complete(..., trigger_needs_context=True)`. Distinct from
      `needs_clarification`'s existing test path -- there is no
      cheap-heuristic pre-LLM gate for `needs_context` (see
      `SpecialistAgent.execute()`'s docstring), so this is the only way to
      exercise it deterministically.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Type, get_args, get_origin

from pydantic import BaseModel, ValidationError

from ai_sdlc.capabilities.reasoning import (
    MalformedResponseError,
    ProviderError,
    ReasoningCapability,
    SchemaT,
)

_VALID_FORCE_ERRORS = (None, "malformed", "provider_failure")

_ACTION_VERBS = (
    "add", "support", "implement", "enable", "allow", "provide", "create",
    "build", "integrate", "let", "must", "should", "need", "require",
)

_NFR_KEYWORDS = (
    "performance", "latency", "scalab", "availab", "reliab", "secur",
    "throughput", "uptime", "response time", "concurren", "durab",
)

_SCOPE_KEYWORDS = ("out of scope", "not include", "exclude", "will not", "won't", "not support")

_RISK_KEYWORDS = (
    "risk", "fail", "latency", "security", "breach", "downtime", "outage",
    "scale", "complex", "migration", "dependency",
)

_A11Y_KEYWORDS = (
    "accessib", "keyboard", "screen reader", "screen-reader", "contrast",
    "aria", "wcag", "a11y",
)

_TECH_TOKENS = (
    "Python", "FastAPI", "Django", "Flask", "Node", "TypeScript",
    "JavaScript", "React", "Vue", "Angular", "Java", "Spring", "Kotlin",
    "Go", "Rust", "PostgreSQL", "Postgres", "MySQL", "MongoDB", "Redis",
    "Kafka", "RabbitMQ", "Docker", "Kubernetes", "AWS", "GCP", "Azure",
    "GraphQL", "REST", "gRPC",
)

_DEFAULT_TECH_STACK = ["Python", "FastAPI", "PostgreSQL"]

_NO_UI_KEYWORDS = (
    "console", "cli", "command line", "command-line", "stdout", "print",
    "backend", "headless", "library", "background job", "cron job",
    "api only", "api-only", "batch job", "script",
)
_UI_KEYWORDS = (
    " ui", "gui", "screen", " page", "button", "form", "dashboard", " view",
    "display", "interface", "web", "frontend", "front-end", "click",
    "browser", "mobile app",
)


def _extract_content(prompt: str) -> str:
    """Isolate the actual input content from a full agent prompt.

    Craft's prompt builders (see `agents/po/prompts.py`,
    `agents/architecture/prompts.py`) wrap the caller-supplied content
    (raw requirement text / structured requirements block) in
    triple-double-quoted blocks, distinct from the fixed
    role/constraints/output-structure boilerplate that surrounds it. A real
    LLM would use the whole prompt as context; this mock is rule-based, so
    it deliberately narrows its parsing to just that content -- otherwise
    it would derive "requirements" out of the prompt's own instructions.
    Falls back to the full prompt if no such block is present (e.g. a
    prompt built by some future agent that doesn't follow the convention).
    """
    blocks = re.findall(r'"""(.*?)"""', prompt or "", flags=re.DOTALL)
    if blocks:
        return "\n".join(blocks)
    return prompt or ""


def _sentences(text: str) -> List[str]:
    raw = re.split(r"[\n\r.;]+", text or "")
    return [s.strip() for s in raw if s.strip()]


def _match_any(sentence: str, keywords: tuple) -> bool:
    lowered = sentence.lower()
    return any(k in lowered for k in keywords)


class MockReasoningProvider(ReasoningCapability):
    def __init__(self, force_error: Optional[str] = None, trigger_needs_context: bool = False):
        if force_error not in _VALID_FORCE_ERRORS:
            raise ValueError(
                f"Unsupported force_error={force_error!r}; expected one of {_VALID_FORCE_ERRORS}"
            )
        self.force_error = force_error
        self.trigger_needs_context = bool(trigger_needs_context)

    def complete(
        self,
        prompt: str,
        *,
        output_schema: Type[SchemaT],
        force_error: Optional[str] = None,
        trigger_needs_context: Optional[bool] = None,
    ) -> SchemaT:
        effective = force_error if force_error is not None else self.force_error
        if effective not in _VALID_FORCE_ERRORS:
            raise ValueError(
                f"Unsupported force_error={effective!r}; expected one of {_VALID_FORCE_ERRORS}"
            )
        effective_needs_context = (
            trigger_needs_context if trigger_needs_context is not None else self.trigger_needs_context
        )

        if effective == "provider_failure":
            raise ProviderError("mock_provider: simulated provider/network failure")

        payload = self._derive_payload(prompt, output_schema)

        if effective_needs_context and "needs_context" in output_schema.model_fields:
            payload["needs_context"] = True
            payload["context_query"] = (
                "What does the existing project documentation say about this requirement?"
            )
            payload["needs_clarification"] = False

        if effective == "malformed":
            payload = self._malform(payload, output_schema)

        try:
            return output_schema(**payload)
        except ValidationError as exc:
            raise MalformedResponseError(
                f"mock_provider: generated response failed schema validation: {exc}"
            ) from exc

    # -- payload generation --------------------------------------------

    def _derive_payload(self, prompt: str, output_schema: Type[BaseModel]) -> Dict[str, Any]:
        sentences = _sentences(_extract_content(prompt))
        payload: Dict[str, Any] = {}
        for name, field in output_schema.model_fields.items():
            annotation = field.annotation
            origin = get_origin(annotation)
            if name in ("clarification_question", "context_query"):
                # Optional[str], paired with `needs_clarification`/
                # `needs_context` respectively (see `_bool_value`) -- the
                # mock never asks the LLM-driven clarification/context
                # question (that's deliberately not something a
                # keyword-rule mock can honestly simulate), so this is
                # always unused/None here. `complete()` overrides
                # `context_query` directly when `trigger_needs_context` is
                # in effect. Handled by name before the type-based dispatch
                # below since `Optional[str]` would otherwise fall into the
                # generic `_str_value` branch and get populated with unused
                # placeholder text.
                payload[name] = None
            elif origin in (list, List):
                payload[name] = self._list_value(name, sentences)
            elif annotation is bool:
                payload[name] = self._bool_value(name, sentences)
            else:
                payload[name] = self._str_value(name, sentences, prompt)
        return payload

    def _malform(self, payload: Dict[str, Any], output_schema: Type[BaseModel]) -> Dict[str, Any]:
        """Deliberately corrupt a valid payload so schema validation fails.

        Drops one *required* field entirely (rather than e.g. setting it to
        an empty string) so this works generically across any schema Craft
        defines, without needing to know that schema's specific field
        names/validators. Must specifically be a required field (no
        default) -- since `needs_clarification`/`clarification_question`
        both have defaults, dropping either of those wouldn't actually
        break validation (Pydantic would just apply the default), which is
        exactly the bug this used to have when it dropped whichever key
        sorted first regardless of whether it was required.
        """
        corrupted = dict(payload)
        required_keys = sorted(name for name in corrupted if output_schema.model_fields[name].is_required())
        if required_keys:
            corrupted.pop(required_keys[0])
        return corrupted

    def _bool_value(self, name: str, sentences: List[str]) -> bool:
        """Rule-based value for a boolean field, keyed by field name --
        same style as `_str_value`/`_list_value`.

        `needs_clarification` (PO/Architecture/UX, paired with
        `clarification_question` -- see `_derive_payload`) is always
        False: this mock is a deterministic wiring oracle, not a stand-in
        for a real model's judgment about ambiguity -- the pre-LLM
        `check_needs_clarification()` heuristic gate (see each agent's
        `*_agent.py`) is what mock-backed tests exercise for clarification
        behavior instead. `needs_context` is likewise always False here for
        the same reason -- `complete()`'s `trigger_needs_context` hook is
        what mock-backed tests exercise for that behavior instead, since
        there's no cheap pre-LLM heuristic for it at all. Any other bool
        field defaults to True (currently only `requires_ui`, Architecture
        Agent -- assume a UI is needed unless a clear no-UI signal is found
        in the input, matching the schema's own conservative default)
        since it's the only other bool field that exists today.
        """
        lname = name.lower()
        if "needs_clarification" in lname or "needs_context" in lname:
            return False
        if "requires_ui" not in lname:
            return True

        joined = f" {' '.join(sentences).lower()} "
        has_no_ui_signal = any(k in joined for k in _NO_UI_KEYWORDS)
        has_ui_signal = any(k in joined for k in _UI_KEYWORDS)
        return not (has_no_ui_signal and not has_ui_signal)

    def _str_value(self, name: str, sentences: List[str], prompt: str) -> str:
        lname = name.lower()
        if not sentences:
            return f"No input detail available to derive {lname.replace('_', ' ')}."

        if "title" in lname:
            base = sentences[0]
            words = base.split()
            return " ".join(w.capitalize() for w in words[:8]) or "Untitled"

        if "summary" in lname:
            return ". ".join(sentences[:2]).strip() + "."

        if "rationale" in lname:
            reasoning = [s for s in sentences if _match_any(s, ("because", "so that", "in order to", "to reduce", "to improve", "to ensure"))]
            source = reasoning or sentences
            return (
                "Derived from the stated requirements: "
                + "; ".join(source[:3])
                + "."
            )

        # generic fallback: first sentence
        return sentences[0]

    def _list_value(self, name: str, sentences: List[str]) -> List[str]:
        lname = name.lower()

        if "functional" in lname and "non" not in lname and "acceptance" not in lname:
            matches = [s for s in sentences if _match_any(s, _ACTION_VERBS)]
            result = matches or sentences
            return [f"System shall: {s}." for s in result[:5]] or ["System shall satisfy the stated requirement."]

        if "non_functional" in lname or "nonfunctional" in lname:
            matches = [s for s in sentences if _match_any(s, _NFR_KEYWORDS)]
            if matches:
                return [f"System shall satisfy: {s}." for s in matches[:5]]
            return [
                "System shall remain reliable and maintainable under normal load.",
                "System shall respond within an acceptable latency budget.",
            ]

        if "out_of_scope" in lname or "scope" in lname:
            matches = [s for s in sentences if _match_any(s, _SCOPE_KEYWORDS)]
            if matches:
                return [s for s in matches[:5]]
            return ["Anything not explicitly described in the requirement is out of scope for this change."]

        if "acceptance" in lname:
            basis = [s for s in sentences if _match_any(s, _ACTION_VERBS)] or sentences
            return [f"Verify that: {s}." for s in basis[:5]] or ["Verify that the requirement is met."]

        if "tech_stack" in lname or "stack" in lname:
            found = []
            joined = " ".join(sentences)
            for token in _TECH_TOKENS:
                if re.search(rf"\b{re.escape(token)}\b", joined, flags=re.IGNORECASE):
                    found.append(token)
            return found or list(_DEFAULT_TECH_STACK)

        if "component" in lname:
            basis = sentences[:5] or ["core module"]
            return [f"Modify component to satisfy: {s}." for s in basis]

        if "decision" in lname:
            basis = sentences[:5] or ["baseline architecture"]
            return [f"Adopt approach addressing: {s}." for s in basis]

        if "risk" in lname:
            matches = [s for s in sentences if _match_any(s, _RISK_KEYWORDS)]
            if matches:
                return [f"Risk: {s}." for s in matches[:5]]
            return ["Risk: requirement scope may expand once implementation begins."]

        if "flow" in lname:
            matches = [s for s in sentences if _match_any(s, _ACTION_VERBS)]
            basis = matches or sentences
            return [f"User flow: {s}." for s in basis[:5]] or ["User flow: complete the primary task described in the requirement."]

        if "screen" in lname:
            basis = sentences[:5] or ["primary view"]
            return [f"Screen to support: {s}." for s in basis]

        if "accessib" in lname:
            matches = [s for s in sentences if _match_any(s, _A11Y_KEYWORDS)]
            if matches:
                return [f"Accessibility: {s}." for s in matches[:5]]
            return [
                "Ensure all interactive elements are keyboard-navigable.",
                "Ensure sufficient color contrast and screen-reader labeling for new UI elements.",
            ]

        # generic fallback
        return sentences[:5] or [f"Derived {lname.replace('_', ' ')} item."]
