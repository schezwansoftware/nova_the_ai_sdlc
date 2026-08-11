"""Reasoning capability abstraction.

This is the boundary specialist agents (Craft) call through instead of
depending on a specific LLM vendor SDK. Concrete providers implement
`ReasoningCapability.complete()`; agents only ever see this interface.

Conceptually:

    PO / Architecture Agent
            |
            v
    ReasoningCapability   (this module)
            |
            v
    ReasoningProvider Protocol (providers/base.py)
            |
            v
    Configured provider (providers/mock.py, the hard default; or
    providers/reasoning_anthropic.py's AnthropicReasoningProvider, the
    real V1 provider, selected per `providers/reasoning_factory.py`)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Type, TypeVar

from pydantic import BaseModel

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class ProviderError(Exception):
    """Raised when the underlying provider fails to produce a response at
    all (e.g. network failure, rate limit, vendor outage). Callers should
    generally treat this as a retryable condition."""


class MalformedResponseError(Exception):
    """Raised when the underlying provider *did* respond, but the response
    could not be parsed/validated into the requested output schema."""


class ReasoningCapability(ABC):
    """Abstract reasoning capability.

    Agents call `complete(prompt, output_schema=...)` and receive back a
    validated instance of `output_schema`. Implementations are responsible
    for whatever prompting/parsing/validation is needed to satisfy the
    schema, and must raise `ProviderError` or `MalformedResponseError`
    (never an arbitrary/unrelated exception) on failure so callers can
    handle failures uniformly regardless of which provider is configured.
    """

    @abstractmethod
    def complete(self, prompt: str, *, output_schema: Type[SchemaT]) -> SchemaT:
        """Run a reasoning completion and return a validated `output_schema`
        instance.

        Raises:
            ProviderError: the provider could not produce a response.
            MalformedResponseError: the provider responded, but the
                response does not satisfy `output_schema`.
        """
        raise NotImplementedError()
