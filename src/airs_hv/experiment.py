from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .pipeline import run_pipeline
from .schema import PipelineConfig
from .trace import _json_default as trace_json_default


def run_experiment(
    *,
    suite_path: Path,
    models: Iterable[str],
    output_dir: Path,
    samples_per_prompt: int = 1,
    temperature: float | None = None,
    reasoning_effort: str | None = None,
    max_tokens: int | None = None,
    retries: int = 3,
    request_timeout: float = 60.0,
    gateway_base_url: str | None = None,
    gateway_model_override: str | None = None,
    gateway_model_map_path: Path | None = None,
    skip_dynamic: bool = False,
    skip_urls: bool = False,
    save_code: bool = False,
    save_raw_output: bool = False,
) -> Dict[str, Any]:
    """
    Run the full pipeline independently for each model target and compare results.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    reports: List[Dict[str, Any]] = []
    for model_target in models:
        model_output_dir = output_dir / _safe_path_component(model_target)
        report = run_pipeline(
            PipelineConfig(
                suite_path=suite_path,
                output_dir=model_output_dir,
                model=model_target,
                samples_per_prompt=samples_per_prompt,
                temperature=temperature,
                reasoning_effort=reasoning_effort,
                max_tokens=max_tokens,
                retries=retries,
                request_timeout=request_timeout,
                gateway_base_url=gateway_base_url,
                gateway_model_override=gateway_model_override,
                gateway_model_map_path=gateway_model_map_path,
                skip_dynamic=skip_dynamic,
                skip_urls=skip_urls,
                save_code=save_code,
                save_raw_output=save_raw_output,
            )
        )
        reports.append(
            {
                "model": model_target,
                "output_dir": str(model_output_dir),
                "report": report,
            }
        )

    comparison = {
        "models": [entry["model"] for entry in reports],
        "runs": reports,
        "per_model_comparison": {
            entry["model"]: {
                "total_samples": entry["report"]["total_samples"],
                "total_failures": entry["report"]["total_failures"],
                "aifr": entry["report"]["aifr"],
                "avg_risk_score": entry["report"]["avg_risk_score"],
                "metric_summary": entry["report"]["metric_summary"],
                "warnings_summary": entry["report"].get("warnings_summary", {}),
            }
            for entry in reports
        },
    }
    comparison_path = output_dir / "comparison.jsonl"
    with comparison_path.open("w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                comparison,
                default=trace_json_default,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )
    return comparison


def _safe_path_component(value: str) -> str:
    return "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in value)
