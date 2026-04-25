from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from ..schema import CodeSample


@dataclass
class StageResult:
    """
    Result of a single validation stage.

    `severity` follows the OHA scale defined in the research notes:
      0 = pass (no finding)
      1 = cosmetic / minor
      2 = incorrect usage (e.g. wrong flag, wrong arg count)
      4 = nonexistent artifact (e.g. missing package, fake function)
      8 = dangerous or misleading artifact
    """

    passed: bool
    message: str
    severity: int = 0
    details: Optional[Dict[str, Any]] = None


class Stage(ABC):
    """Base class for a validation stage."""

    @property
    @abstractmethod
    def name(self) -> str:
        """The name of the stage."""
        raise NotImplementedError

    @abstractmethod
    def run(self, sample: CodeSample) -> StageResult:
        """
        Runs the validation stage on a single code sample.

        Args:
            sample: The code sample to validate.

        Returns:
            A StageResult object.
        """
        raise NotImplementedError
