from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class GenerationTrace:
    """Comparable request/response metadata captured for every model call."""

    provider: str
    model_name: str
    model_target: str
    request: Dict[str, Any] = field(default_factory=dict)
    response: Dict[str, Any] = field(default_factory=dict)


class ModelClient(ABC):
    """Provider-neutral client interface for code generation."""

    @property
    @abstractmethod
    def provider(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def model_name(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def model_target(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def last_trace(self) -> Optional[GenerationTrace]:
        raise NotImplementedError

    @abstractmethod
    def generate(self, prompt: str, *, prompt_id: str | None = None) -> str:
        """Generate raw code directly from the provider API."""
        raise NotImplementedError
