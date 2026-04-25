from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict


class TraceLogger:
    """Append-only JSONL logger for full pipeline traceability."""

    def __init__(self, path: Path, run_id: str):
        self.path = path
        self.run_id = run_id
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event: str, **payload: Any) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "event": event,
            **payload,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    record,
                    default=_json_default,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )


def _json_default(obj: Any) -> Any:
    usage_object = _coerce_response_usage(obj)
    if usage_object is not None:
        return usage_object
    if isinstance(obj, Enum):
        return obj.value
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, set):
        return sorted(obj)
    model_dump = getattr(obj, "model_dump", None)
    if callable(model_dump):
        return model_dump()
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)


def _coerce_response_usage(obj: Any) -> Dict[str, Any] | None:
    usage_fields = ("prompt_tokens", "completion_tokens", "total_tokens")
    values = {
        field: getattr(obj, field)
        for field in usage_fields
        if hasattr(obj, field) and getattr(obj, field) is not None
    }
    return values or None


def summarize_sample_for_trace(
    *,
    prompt_id: str,
    sample_id: Any,
    prompt: str,
    model: str,
    raw_output: str,
    generation_meta: Dict[str, Any],
    warnings: list[dict[str, Any]],
    evaluation_results: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "prompt_id": prompt_id,
        "sample_id": sample_id,
        "prompt": prompt,
        "model": model,
        "raw_output": raw_output,
        "generation_meta": generation_meta,
        "warnings": warnings,
        "evaluation_results": evaluation_results,
    }
