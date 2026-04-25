"""
Pipeline orchestration for the AIRS Hallucination Validation project.

Stages:
  1. Load prompts from JSONL
  2. Generate code via a real model API
  3. Optionally save the raw generated artifact
  4. Evaluate the artifact with hallucination-focused metrics
  5. Log trace events and aggregate results
"""

from __future__ import annotations

import hashlib
import json
import math
import traceback
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, List
from uuid import uuid4

from .failure_checks import (
    METRIC_API,
    METRIC_CLI,
    METRIC_DEPENDENCY,
    METRIC_EXECUTABLE,
    METRIC_RECURRENT,
    METRIC_REQUIREMENT,
    prepare_artifact_metadata,
    run_failure_checks,
)
from .generator import build_model_client, resolve_model_selection
from .hallucination_checks import (
    evaluate_samples_and_write_outputs,
    run_adversarial_self_checks as run_hallucination_adversarial_self_checks,
    write_global_failure_summary_outputs,
)
from .trace import TraceLogger, _json_default as trace_json_default, summarize_sample_for_trace

if TYPE_CHECKING:
    from .generator import ModelClient
    from .schema import CodeSample, PipelineConfig, Prompt


def run_pipeline(config: "PipelineConfig") -> Dict[str, Any]:
    """Execute the full validation pipeline and return the aggregate report."""
    from .schema import CodeSample

    selected_models = resolve_model_selection(config.model)
    if config.gateway_model_override and len(selected_models) != 1:
        raise ValueError("--gateway-model can only be used with a single model alias.")
    prompt_files = resolve_input_prompt_files(Path(config.suite_path))
    runs_per_prompt = config.effective_runs_per_prompt
    if runs_per_prompt < 1:
        raise ValueError("--runs-per-prompt must be at least 1.")

    run_id = uuid4().hex
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    bundles_dir = output_dir / "bundles"
    bundles_dir.mkdir(parents=True, exist_ok=True)
    trace_logger = TraceLogger(output_dir / "trace.jsonl", run_id)

    trace_logger.log(
        "pipeline_started",
        model_selection=config.model,
        selected_models=selected_models,
        gateway_model_override=config.gateway_model_override,
        input_file=str(config.suite_path),
        prompt_files=[str(path) for path in prompt_files],
        config=_sanitized_pipeline_config(config),
    )

    adversarial_checks = _run_adversarial_injection_checks()
    hallucination_adversarial_checks = None
    if config.run_failure_checks:
        hallucination_adversarial_checks = run_hallucination_adversarial_self_checks(
            recurrence_threshold=config.recurrence_threshold,
            disable_sandbox=False,
        )
    clients = {
        model_alias: build_model_client(model_alias, _build_model_client_config(config))
        for model_alias in selected_models
    }

    input_path = Path(config.suite_path)
    if input_path.is_dir():
        print(f"Input path is a folder: {input_path}")
        print(f"Found {len(prompt_files)} JSONL prompt file(s):")
        for prompt_file in prompt_files:
            print(f"- {prompt_file}")
    else:
        print(f"Input path is a file: {input_path}")
    print(
        "Adversarial self-checks passed: "
        f"{adversarial_checks['total_cases']} injected case(s)"
    )
    if hallucination_adversarial_checks:
        print(
            "Hallucination failure self-checks passed: "
            f"{hallucination_adversarial_checks['total_cases']} injected case(s)"
        )
    print(f"Selected models: {selected_models}")
    print(f"Runs per prompt: {runs_per_prompt}")
    if config.gateway_model_override:
        print(f"Gateway model override: {config.gateway_model_override}")
    all_samples: List[CodeSample] = []
    historical_outputs: List[Dict[str, Any]] = []
    execution_errors: List[Dict[str, Any]] = []
    group_output_summaries: List[Dict[str, Any]] = []
    global_failure_records: List[Dict[str, Any]] = []
    global_generation_errors: List[Dict[str, Any]] = []
    prompt_load_stats = _empty_prompt_load_stats()
    prior_output_index = _load_prior_output_index(output_dir)
    current_output_index: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    sample_index = 0
    for prompt_file in prompt_files:
        dataset_name = _safe_path_component(prompt_file.stem.replace(" ", "_"))
        prompts, file_load_stats = _load_prompts(prompt_file, trace_logger=trace_logger)
        _merge_prompt_load_stats(prompt_load_stats, prompt_file, file_load_stats)
        if not prompts:
            if len(prompt_files) == 1:
                raise ValueError(
                    f"No valid prompts were loaded from {prompt_file}. "
                    "Input must be JSONL with prompt_id and prompt fields."
                )
            print(f"Skipping dataset={dataset_name}: no valid prompts loaded.")
            continue

        for prompt in prompts:
            prompt.prompt_file = prompt_file.name
            prompt.prompt_file_stem = dataset_name

        print(f"Loaded {len(prompts)} valid prompt(s) from {prompt_file}")
        if file_load_stats["skipped_invalid_lines"] or file_load_stats["skipped_empty_lines"]:
            print(
                "Skipped prompt lines: "
                f"empty={file_load_stats['skipped_empty_lines']} "
                f"invalid={file_load_stats['skipped_invalid_lines']}"
            )

        for model_alias in selected_models:
            client = clients[model_alias]
            group_samples: List[CodeSample] = []
            group_errors: List[Dict[str, Any]] = []
            group_history: List[Dict[str, Any]] = []
            print(
                f"\nProcessing dataset={dataset_name} model={model_alias} "
                f"prompts={len(prompts)} runs_per_prompt={runs_per_prompt}"
            )
            for prompt in prompts:
                for run_sequence_id in range(1, runs_per_prompt + 1):
                    print(
                        f"Generating prompt={prompt.prompt_id} "
                        f"run={run_sequence_id}/{runs_per_prompt} model={model_alias}"
                    )
                    try:
                        sample = _run_single_sample(
                            idx=sample_index,
                            sample_number=run_sequence_id - 1,
                            run_sequence_id=run_sequence_id,
                            prompt=prompt,
                            prompt_file=prompt_file.name,
                            prompt_file_stem=dataset_name,
                            client=client,
                            config=config,
                            output_dir=output_dir,
                            bundles_dir=bundles_dir,
                            trace_logger=trace_logger,
                            historical_outputs=group_history,
                            prior_output_index=prior_output_index,
                            current_output_index=current_output_index,
                            pipeline_run_id=run_id,
                            write_legacy_flat_artifact=(len(prompt_files) == 1 and runs_per_prompt == 1),
                        )
                    except Exception as exc:  # noqa: BLE001
                        error_record = _build_error_record(
                            prompt=prompt,
                            model=model_alias,
                            sample_id=sample_index,
                            sample_number=run_sequence_id - 1,
                            run_id=run_sequence_id,
                            prompt_file=prompt_file.name,
                            prompt_file_stem=dataset_name,
                            error=exc,
                        )
                        execution_errors.append(error_record)
                        group_errors.append(error_record)
                        global_generation_errors.append(error_record)
                        trace_logger.log("sample_error", **error_record)
                        print(
                            f"  [{sample_index}] prompt={prompt.prompt_id} model={model_alias} "
                            f"run_id={run_sequence_id} status=error error={type(exc).__name__}: {exc}"
                        )
                        sample_index += 1
                        continue

                    all_samples.append(sample)
                    group_samples.append(sample)
                    history_record = _build_history_record(sample)
                    historical_outputs.append(history_record)
                    group_history.append(history_record)
                    print(
                        f"  [{sample_index}] prompt={sample.prompt_id} model={sample.model} "
                        f"run_id={sample.run_id} code_len={len(sample.code)} "
                        f"status={sample.failure_report['overall_status']}"
                    )
                    sample_index += 1

            if config.run_failure_checks:
                results_root = Path(config.results_dir) if config.results_dir else output_dir / "results"
                group_results_dir = results_root / _safe_path_component(model_alias) / dataset_name
                failure_check_outputs = evaluate_samples_and_write_outputs(
                    samples=group_samples,
                    generation_errors=group_errors,
                    results_dir=group_results_dir,
                    recurrence_threshold=config.recurrence_threshold,
                    disable_sandbox=config.disable_sandbox or config.skip_dynamic,
                    run_id=run_id,
                )
                group_output_summaries.append(
                    {
                        "model": model_alias,
                        "dataset": dataset_name,
                        "results_dir": failure_check_outputs["results_dir"],
                        "failure_checks_jsonl": failure_check_outputs["failure_checks_jsonl"],
                        "failure_summary_json": failure_check_outputs["failure_summary_json"],
                        "records_evaluated": failure_check_outputs["records_evaluated"],
                        "generation_errors": failure_check_outputs["generation_errors"],
                    }
                )
                global_failure_records.extend(failure_check_outputs.get("records", []))
                print(f"Writing {failure_check_outputs['failure_summary_json']}")

    report = _build_report(
        samples=all_samples,
        errors=execution_errors,
        run_id=run_id,
        model_selection=config.model,
        selected_models=selected_models,
        trace_file="trace.jsonl",
        report_file="report.jsonl",
        prompt_load_stats=prompt_load_stats,
        adversarial_checks=adversarial_checks,
    )
    if hallucination_adversarial_checks:
        report["hallucination_adversarial_checks"] = hallucination_adversarial_checks
    if config.run_failure_checks:
        report["failure_check_outputs"] = {"groups": group_output_summaries}
        if config.write_global_summary:
            results_root = Path(config.results_dir) if config.results_dir else output_dir / "results"
            global_outputs = write_global_failure_summary_outputs(
                records=global_failure_records,
                generation_errors=global_generation_errors,
                results_dir=results_root,
                recurrence_threshold=config.recurrence_threshold,
                run_id=run_id,
            )
            report["failure_check_outputs"]["global"] = global_outputs
    report_path = output_dir / "report.jsonl"
    _write_jsonl_object(report_path, report)

    trace_logger.log(
        "pipeline_completed",
        model_selection=config.model,
        selected_models=selected_models,
        report_path=str(report_path),
        total_samples=report["total_samples"],
        total_failures=report["total_failures"],
        total_errors=report["total_errors"],
        warnings_summary=report.get("warnings_summary", {}),
    )

    print("\nPipeline complete.")
    print(f"  Trace:  {trace_logger.path}")
    print(f"  Report: {report_path}")
    if group_output_summaries:
        print(f"  Result summaries written: {len(group_output_summaries)}")
    print(f"  Models: {selected_models}")
    print(
        f"  samples={report['total_samples']} "
        f"failures={report['total_failures']} errors={report['total_errors']}"
    )
    print("Completed pipeline.")
    print(f"Datasets processed: {len(prompt_files)}")
    print(f"Models processed: {len(selected_models)}")
    print(f"Runs per prompt: {runs_per_prompt}")
    print(f"Artifacts generated: {len(all_samples)}")
    print(f"Result summaries written: {len(group_output_summaries)}")

    if config.fail_on_generation_error and execution_errors:
        raise RuntimeError(
            f"Generation failed for {len(execution_errors)} sample(s); "
            "failure-check outputs were written before stopping."
        )

    return report


def resolve_input_prompt_files(input_path: Path) -> List[Path]:
    """Resolve a JSONL file or a folder of JSONL files into sorted prompt files."""

    if input_path.is_dir():
        prompt_files = sorted(path for path in input_path.glob("*.jsonl") if path.is_file())
        if not prompt_files:
            raise ValueError(f"Input folder has no JSONL files: {input_path}")
        return prompt_files
    if input_path.is_file():
        if input_path.suffix.lower() != ".jsonl":
            raise ValueError(
                f"Prompt input must be JSONL. Received '{input_path.name}'. JSON arrays are not supported."
            )
        return [input_path]
    raise FileNotFoundError(f"Prompt input path not found: {input_path}")


def _empty_prompt_load_stats() -> Dict[str, Any]:
    return {
        "total_lines": 0,
        "loaded_prompts": 0,
        "skipped_empty_lines": 0,
        "skipped_invalid_lines": 0,
        "files": {},
    }


def _merge_prompt_load_stats(
    aggregate: Dict[str, Any],
    prompt_file: Path,
    file_stats: Dict[str, int],
) -> None:
    for key in ("total_lines", "loaded_prompts", "skipped_empty_lines", "skipped_invalid_lines"):
        aggregate[key] += int(file_stats.get(key, 0))
    aggregate["files"][prompt_file.name] = dict(file_stats)


def _run_single_sample(
    *,
    idx: int,
    sample_number: int,
    run_sequence_id: int,
    prompt: "Prompt",
    prompt_file: str,
    prompt_file_stem: str,
    client: "ModelClient",
    config: "PipelineConfig",
    output_dir: Path,
    bundles_dir: Path,
    trace_logger: TraceLogger,
    historical_outputs: List[Dict[str, Any]],
    prior_output_index: Dict[str, List[Dict[str, Any]]],
    current_output_index: Dict[str, List[Dict[str, Any]]],
    pipeline_run_id: str,
    write_legacy_flat_artifact: bool,
) -> "CodeSample":
    from .schema import CodeSample, LLMResponse

    trace_logger.log(
        "generation_requested",
        prompt_id=prompt.prompt_id,
        sample_id=sample_number,
        run_id=run_sequence_id,
        prompt_file=prompt_file,
        prompt_file_stem=prompt_file_stem,
        model=client.model_target,
        model_alias=client.model_target,
        gateway_model=getattr(client, "gateway_model_id", None),
        prompt=prompt.prompt,
    )
    code = client.generate(prompt.prompt, prompt_id=prompt.prompt_id)
    raw_model_output = getattr(client, "last_raw_output", None) or code
    generation_meta = _build_generation_meta(client, run_id=pipeline_run_id)
    generation_meta["raw_output_saved"] = False
    generation_meta["prompt_file"] = prompt_file
    generation_meta["prompt_file_stem"] = prompt_file_stem
    generation_meta["run_id"] = run_sequence_id
    response = LLMResponse(
        prompt_id=prompt.prompt_id,
        sample_id=sample_number,
        model=client.model_target,
        code=code,
        meta=generation_meta,
    )
    sample = CodeSample(
        prompt_id=prompt.prompt_id,
        prompt_source=prompt.prompt,
        response=response,
        prompt_family=prompt.family,
        prompt_contract=prompt.contract,
        prompt_file=prompt_file,
        prompt_file_stem=prompt_file_stem,
        run_id=run_sequence_id,
    )

    if config.save_raw_output:
        raw_files = _write_raw_output(
            output_dir=output_dir,
            prompt_id=sample.prompt_id,
            run_id=run_sequence_id,
            model=sample.model,
            prompt_file_stem=prompt_file_stem,
            raw_output=raw_model_output,
            write_legacy_flat_artifact=write_legacy_flat_artifact,
        )
        generation_meta["raw_output_file"] = raw_files["raw_output_file"]
        generation_meta["grouped_raw_output_file"] = raw_files["grouped_raw_output_file"]
        generation_meta["raw_output_saved"] = True

    sample.warnings = _collect_generation_warnings(
        sample=sample,
        prior_output_index=prior_output_index,
        current_output_index=current_output_index,
    )
    trace_logger.log(
        "generation_completed",
        prompt_id=prompt.prompt_id,
        sample_id=sample.sample_id,
        run_id=sample.run_id,
        prompt_file=prompt_file,
        prompt_file_stem=prompt_file_stem,
        model=sample.model,
        model_alias=sample.model,
        gateway_model=generation_meta.get("request", {}).get("gateway_model"),
        generated_code_length=len(sample.code),
        raw_output=raw_model_output,
        code=sample.code,
        generation_meta=generation_meta,
        warnings=sample.warnings,
    )

    prompt_spec = _build_prompt_spec(prompt)
    sample.artifact_metadata = _normalize_artifact(sample, prompt_spec)
    trace_logger.log(
        "artifact_normalized",
        prompt_id=sample.prompt_id,
        sample_id=sample.sample_id,
        run_id=sample.run_id,
        prompt_file=prompt_file,
        prompt_file_stem=prompt_file_stem,
        model=sample.model,
        artifact_metadata=sample.artifact_metadata,
    )

    sample.failure_report = _evaluate_artifact(
        sample,
        prompt_spec=prompt_spec,
        historical_outputs=historical_outputs,
    )
    sample.stage_results = _build_metric_stage_results(sample.failure_report)
    trace_logger.log(
        "evaluation_completed",
        prompt_id=sample.prompt_id,
        sample_id=sample.sample_id,
        run_id=sample.run_id,
        prompt_file=prompt_file,
        prompt_file_stem=prompt_file_stem,
        model=sample.model,
        model_alias=sample.model,
        gateway_model=generation_meta.get("request", {}).get("gateway_model"),
        evaluation_status=sample.failure_report["overall_status"],
        evaluation_results=sample.failure_report,
    )

    if config.save_code:
        artifact_files = _write_artifact(
            output_dir=output_dir,
            sample=sample,
            write_legacy_flat_artifact=write_legacy_flat_artifact,
        )
        sample.artifact_file = artifact_files["artifact_file"]
        generation_meta["grouped_artifact_file"] = artifact_files["grouped_artifact_file"]
        _append_artifact_metadata(output_dir=output_dir, sample=sample)

    _write_bundle(bundles_dir, idx, sample, run_id=pipeline_run_id)
    trace_logger.log(
        "sample_completed",
        **summarize_sample_for_trace(
            prompt_id=sample.prompt_id,
            sample_id=sample.sample_id,
            prompt=sample.prompt_source,
            model=sample.model,
            raw_output=raw_model_output,
            generation_meta=generation_meta,
            warnings=sample.warnings,
            evaluation_results=sample.failure_report,
        ),
        run_id=sample.run_id,
        prompt_file=prompt_file,
        prompt_file_stem=prompt_file_stem,
        model_alias=sample.model,
        gateway_model=generation_meta.get("request", {}).get("gateway_model"),
        generated_code_length=len(sample.code),
        evaluation_status=sample.failure_report["overall_status"],
        code=sample.code,
        metrics=sample.failure_report,
    )
    return sample


def _load_prompts(path: Path, *, trace_logger: TraceLogger) -> tuple[List["Prompt"], Dict[str, int]]:
    """Load prompts from JSONL only, skipping empty or invalid lines safely."""
    from .schema import Prompt

    if not path.exists():
        raise FileNotFoundError(f"Prompt suite not found: {path}")
    if path.suffix.lower() != ".jsonl":
        raise ValueError(
            f"Prompt input must be JSONL. Received '{path.name}'. JSON arrays are not supported."
        )

    prompts: List[Prompt] = []
    stats = {
        "total_lines": 0,
        "loaded_prompts": 0,
        "skipped_empty_lines": 0,
        "skipped_invalid_lines": 0,
    }
    with path.open("r", encoding="utf-8") as handle:
        for line_num, raw_line in enumerate(handle, start=1):
            stats["total_lines"] += 1
            line = raw_line.strip()
            if not line:
                stats["skipped_empty_lines"] += 1
                trace_logger.log(
                    "prompt_line_skipped",
                    line_num=line_num,
                    reason="empty_line",
                )
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                stats["skipped_invalid_lines"] += 1
                trace_logger.log(
                    "prompt_line_skipped",
                    line_num=line_num,
                    reason="invalid_json",
                    error=str(exc),
                )
                continue

            if not isinstance(record, dict):
                stats["skipped_invalid_lines"] += 1
                trace_logger.log(
                    "prompt_line_skipped",
                    line_num=line_num,
                    reason="not_object",
                )
                continue

            try:
                prompts.append(_prompt_from_record(record))
                stats["loaded_prompts"] += 1
            except ValueError as exc:
                stats["skipped_invalid_lines"] += 1
                trace_logger.log(
                    "prompt_line_skipped",
                    line_num=line_num,
                    reason="invalid_prompt_record",
                    error=str(exc),
                )

    return prompts, stats


def _prompt_from_record(record: Dict[str, Any]) -> "Prompt":
    from .schema import Prompt

    prompt_id = str(record.get("prompt_id") or "").strip()
    prompt_text = record.get("prompt")
    if not prompt_id or not isinstance(prompt_text, str) or not prompt_text.strip():
        raise ValueError("Each JSONL record must contain non-empty 'prompt_id' and 'prompt' fields.")

    return Prompt(
        prompt_id=prompt_id,
        prompt=prompt_text,
        tier=record.get("tier"),
        family=record.get("family"),
        language=record.get("language"),
        tags=list(record.get("tags", [])) if isinstance(record.get("tags", []), list) else [],
        contract=dict(record.get("contract", {})) if isinstance(record.get("contract"), dict) else None,
    )


def _normalize_artifact(sample: "CodeSample", prompt_spec: Dict[str, Any]) -> Dict[str, Any]:
    metadata = dict(sample.response.meta or {})
    metadata["pip_installs"] = list(sample.response.pip_installs)
    if prompt_spec.get("artifact_type"):
        metadata.setdefault("artifact_type", prompt_spec["artifact_type"])
    return prepare_artifact_metadata(sample.code, metadata, prompt_spec)


def _evaluate_artifact(
    sample: "CodeSample",
    *,
    prompt_spec: Dict[str, Any],
    historical_outputs: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return run_failure_checks(
        artifact_id=f"{sample.prompt_id}:{sample.sample_id}",
        artifact=sample.code,
        metadata=sample.artifact_metadata,
        prompt_spec=prompt_spec,
        historical_outputs=historical_outputs,
    )


def _build_generation_meta(client: "ModelClient", *, run_id: str) -> Dict[str, Any]:
    trace = client.last_trace
    if trace is None:
        return {"run_id": run_id}
    return {
        "run_id": run_id,
        "provider": trace.provider,
        "model_name": trace.model_name,
        "model_target": trace.model_target,
        "request": trace.request,
        "response": trace.response,
    }


def _collect_generation_warnings(
    *,
    sample: "CodeSample",
    prior_output_index: Dict[str, List[Dict[str, Any]]],
    current_output_index: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    output_hash = hashlib.sha256(sample.code.encode("utf-8")).hexdigest()
    warnings: List[Dict[str, Any]] = []

    prior_matches = prior_output_index.get(output_hash, [])
    if prior_matches:
        warnings.append(
            {
                "type": "duplicate_output_previous_run",
                "message": "Generated output matches an artifact already stored in the output directory.",
                "output_sha256": output_hash,
                "matches": prior_matches,
            }
        )

    current_matches = current_output_index.get(output_hash, [])
    if current_matches:
        warnings.append(
            {
                "type": "duplicate_output_current_run",
                "message": "Generated output matches another artifact produced in the current run.",
                "output_sha256": output_hash,
                "matches": current_matches,
            }
        )

    current_output_index[output_hash].append(
        {
            "prompt_id": sample.prompt_id,
            "sample_id": sample.sample_id,
            "model": sample.model,
        }
    )
    return warnings


def _load_prior_output_index(output_dir: Path) -> Dict[str, List[Dict[str, Any]]]:
    index: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    bundles_dir = output_dir / "bundles"
    if not bundles_dir.exists():
        return index

    for bundle_path in bundles_dir.glob("*.jsonl"):
        try:
            bundle = json.loads(bundle_path.read_text(encoding="utf-8").strip())
        except json.JSONDecodeError:
            continue
        code = str(bundle.get("code") or "")
        if not code:
            continue
        output_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        index[output_hash].append(
            {
                "prompt_id": bundle.get("prompt_id"),
                "sample_id": bundle.get("sample_id"),
                "model": bundle.get("model"),
                "bundle": bundle_path.name,
            }
        )
    return index


def _write_bundle(
    bundles_dir: Path,
    idx: int,
    sample: "CodeSample",
    *,
    run_id: str,
) -> None:
    safe_prompt_id = _safe_path_component(sample.prompt_id)
    safe_sample_id = _safe_path_component(str(sample.sample_id))
    bundle_path = bundles_dir / f"{idx}_{safe_prompt_id}_{safe_sample_id}.jsonl"
    bundle = {
        "run_id": run_id,
        "prompt_id": sample.prompt_id,
        "prompt_file": sample.prompt_file,
        "prompt_file_stem": sample.prompt_file_stem,
        "sample_run_id": sample.run_id,
        "prompt_source": sample.prompt_source,
        "prompt_family": sample.prompt_family,
        "sample_id": sample.sample_id,
        "model": sample.model,
        "code": sample.code,
        "pip_installs": sample.response.pip_installs,
        "meta": sample.response.meta,
        "prompt_contract": sample.prompt_contract,
        "artifact_metadata": sample.artifact_metadata,
        "artifact_file": sample.artifact_file,
        "warnings": sample.warnings,
        "stage_results": sample.stage_results,
        "failure_report": sample.failure_report,
    }
    _write_jsonl_object(bundle_path, bundle)


def _write_artifact(
    *,
    output_dir: Path,
    sample: "CodeSample",
    write_legacy_flat_artifact: bool,
) -> Dict[str, str]:
    grouped_path = (
        output_dir
        / _safe_path_component(sample.model)
        / _safe_path_component(sample.prompt_file_stem or "unknown")
        / "artifacts"
        / _group_artifact_filename(sample)
    )
    grouped_path.parent.mkdir(parents=True, exist_ok=True)
    grouped_path.write_text(sample.code, encoding="utf-8")

    artifact_file = str(grouped_path.relative_to(output_dir))
    if write_legacy_flat_artifact:
        legacy_path = output_dir / _legacy_artifact_filename(sample)
        legacy_path.write_text(sample.code, encoding="utf-8")
        artifact_file = str(legacy_path.relative_to(output_dir))

    return {
        "artifact_file": artifact_file,
        "grouped_artifact_file": str(grouped_path),
    }


def _write_raw_output(
    *,
    output_dir: Path,
    prompt_id: str,
    run_id: int,
    model: str,
    prompt_file_stem: str,
    raw_output: str,
    write_legacy_flat_artifact: bool,
) -> Dict[str, str]:
    grouped_raw_dir = (
        output_dir
        / _safe_path_component(model)
        / _safe_path_component(prompt_file_stem)
        / "raw"
    )
    grouped_raw_dir.mkdir(parents=True, exist_ok=True)
    safe_prompt_id = _safe_path_component(prompt_id)
    raw_path = grouped_raw_dir / f"{safe_prompt_id}_run{run_id:02d}.txt"
    raw_path.write_text(raw_output, encoding="utf-8")

    raw_output_file = str(raw_path.relative_to(output_dir))
    if write_legacy_flat_artifact:
        legacy_raw_dir = output_dir / "raw"
        legacy_raw_dir.mkdir(parents=True, exist_ok=True)
        safe_model = _safe_path_component(model.replace(":", "_"))
        legacy_raw_path = legacy_raw_dir / f"{safe_prompt_id}_{safe_model}.txt"
        legacy_raw_path.write_text(raw_output, encoding="utf-8")
        raw_output_file = str(legacy_raw_path.relative_to(output_dir))

    return {
        "raw_output_file": raw_output_file,
        "grouped_raw_output_file": str(raw_path),
    }


def _append_artifact_metadata(*, output_dir: Path, sample: "CodeSample") -> None:
    artifact_log_path = output_dir / "artifacts.jsonl"
    metadata_record = {
        "prompt_id": sample.prompt_id,
        "sample_id": sample.sample_id,
        "model": sample.model,
        "run_id": sample.run_id,
        "prompt_file": sample.prompt_file,
        "prompt_file_stem": sample.prompt_file_stem,
        "artifact_file": sample.artifact_file,
        "grouped_artifact_file": sample.response.meta.get("grouped_artifact_file"),
        "raw_output_file": sample.response.meta.get("raw_output_file"),
        "grouped_raw_output_file": sample.response.meta.get("grouped_raw_output_file"),
        "artifact_type": sample.artifact_metadata.get("artifact_type"),
        "generated_code_length": len(sample.code),
        "warnings": sample.warnings,
    }
    with artifact_log_path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                metadata_record,
                default=trace_json_default,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )


def _group_artifact_filename(sample: "CodeSample") -> str:
    safe_prompt_id = _safe_path_component(sample.prompt_id)
    return f"{safe_prompt_id}_run{int(sample.run_id or 1):02d}{_artifact_extension(sample)}"


def _legacy_artifact_filename(sample: "CodeSample") -> str:
    safe_prompt_id = _safe_path_component(sample.prompt_id)
    safe_model = _safe_path_component(sample.model.replace(":", "_"))
    base_name = f"{safe_prompt_id}_{safe_model}"
    if str(sample.sample_id) not in {"0", "None"}:
        base_name = f"{base_name}_sample_{_safe_path_component(str(sample.sample_id))}"
    return f"{base_name}{_artifact_extension(sample)}"


def _artifact_extension(sample: "CodeSample") -> str:
    artifact_type = str(sample.artifact_metadata.get("artifact_type", "")).lower()
    if artifact_type == "command":
        return ".sh"
    if artifact_type == "code":
        return ".py"
    return ".txt"


def _build_report(
    *,
    samples: List["CodeSample"],
    errors: List[Dict[str, Any]],
    run_id: str,
    model_selection: str,
    selected_models: List[str],
    trace_file: str,
    report_file: str,
    prompt_load_stats: Dict[str, int],
    adversarial_checks: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    total_samples = len(samples)
    failures_by_stage: Dict[str, int] = defaultdict(int)
    metric_totals: Dict[str, Dict[str, float]] = defaultdict(
        lambda: {"samples": 0.0, "failures": 0.0, "score_total": 0.0}
    )
    hallucination_type_distribution: Dict[str, int] = defaultdict(int)
    warning_distribution: Dict[str, int] = defaultdict(int)
    total_severity = 0
    total_failures = 0
    samples_by_family: Dict[str, List[int]] = defaultdict(list)
    samples_by_model: Dict[str, List[int]] = defaultdict(list)
    samples_by_prompt: Dict[str, List[int]] = defaultdict(list)
    risk_scores: List[float] = []
    risk_scores_by_model: Dict[str, List[float]] = defaultdict(list)
    prompts_by_model: Dict[str, set[str]] = defaultdict(set)

    for sample in samples:
        for warning in sample.warnings:
            warning_distribution[str(warning.get("type", "unknown"))] += 1
        for stage_result in sample.stage_results:
            if not stage_result["passed"]:
                failures_by_stage[stage_result["stage"]] += 1
                total_severity += int(stage_result.get("severity", 0))

        has_failure = (
            sample.failure_report["overall_status"] == "fail"
            if sample.failure_report
            else any(not stage["passed"] for stage in sample.stage_results)
        )
        total_failures += 1 if has_failure else 0
        if sample.failure_report:
            risk_scores.append(float(sample.failure_report.get("risk_score", 0.0)))
            for metric_result in sample.failure_report.get("metric_results", []):
                metric_name = str(metric_result["metric"])
                metric_totals[metric_name]["samples"] += 1
                metric_totals[metric_name]["score_total"] += float(metric_result.get("score", 0.0))
                if metric_result.get("status") == "fail":
                    metric_totals[metric_name]["failures"] += 1
            for flag in sample.failure_report.get("hallucination_flags", []):
                hallucination_type_distribution[str(flag.get("type", "unknown"))] += 1

        family_key = sample.prompt_family or "unknown"
        samples_by_family[family_key].append(1 if has_failure else 0)
        samples_by_model[sample.model].append(1 if has_failure else 0)
        samples_by_prompt[sample.prompt_id].append(1 if has_failure else 0)
        risk_scores_by_model[sample.model].append(
            float(sample.failure_report.get("risk_score", 0.0))
            if sample.failure_report
            else 0.0
        )
        prompts_by_model[sample.model].add(sample.prompt_id)

    total_fail_events = sum(failures_by_stage.values())
    metric_summary = {
        metric: {
            "samples": int(values["samples"]),
            "failures": int(values["failures"]),
            "failure_rate": (values["failures"] / values["samples"]) if values["samples"] else 0.0,
            "average_score": (values["score_total"] / values["samples"]) if values["samples"] else 0.0,
        }
        for metric, values in metric_totals.items()
    }

    return {
        "run_id": run_id,
        "model_selection": model_selection,
        "selected_models": selected_models,
        "trace_file": trace_file,
        "report_file": report_file,
        "prompt_load": prompt_load_stats,
        "total_samples": total_samples,
        "total_failures": total_failures,
        "total_errors": len(errors),
        "errors": errors,
        "total_severity": total_severity,
        "aifr": (total_failures / total_samples) if total_samples else 0.0,
        "fss": (total_severity / total_samples) if total_samples else 0.0,
        "models_used": sorted(samples_by_model),
        "model_summary": {
            model: {
                "samples": len(flags),
                "failures": sum(flags),
                "aifr": (sum(flags) / len(flags)) if flags else 0.0,
                "average_risk_score": (
                    sum(risk_scores_by_model[model]) / len(risk_scores_by_model[model])
                )
                if risk_scores_by_model[model]
                else 0.0,
                "prompt_ids": sorted(prompts_by_model[model]),
            }
            for model, flags in sorted(samples_by_model.items())
        },
        "artifact_type_distribution": {
            stage: {
                "failure_count": count,
                "share": (count / total_fail_events) if total_fail_events else 0.0,
            }
            for stage, count in failures_by_stage.items()
        },
        "metric_summary": metric_summary,
        "hallucination_type_distribution": dict(hallucination_type_distribution),
        "warnings_summary": dict(warning_distribution),
        "avg_risk_score": (sum(risk_scores) / len(risk_scores)) if risk_scores else 0.0,
        "aifr_by_framing": {
            family: {
                "samples": len(flags),
                "failures": sum(flags),
                "aifr": (sum(flags) / len(flags)) if flags else 0.0,
            }
            for family, flags in samples_by_family.items()
        },
        "generation_stability": {
            prompt_id: {
                "samples": len(flags),
                "aifr": (sum(flags) / len(flags)) if flags else 0.0,
                "std_dev": _std_dev(flags),
            }
            for prompt_id, flags in samples_by_prompt.items()
        },
        "adversarial_checks": adversarial_checks or {},
    }


def _std_dev(values: Iterable[int]) -> float:
    values = list(values)
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return math.sqrt(variance)


def _safe_path_component(value: str) -> str:
    return "".join(char if char.isalnum() or char in ("-", "_", ".") else "_" for char in value)


def _write_jsonl_object(path: Path, record: Dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                record,
                default=trace_json_default,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )


def _build_metric_stage_results(failure_report: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    if not failure_report:
        return []
    results: List[Dict[str, Any]] = []
    for metric_result in failure_report.get("metric_results", []):
        issues = list(metric_result.get("issues", []))
        results.append(
            {
                "stage": metric_result["metric"],
                "passed": metric_result.get("status") == "pass",
                "severity": _estimate_metric_severity(metric_result),
                "message": (
                    "Metric passed cleanly."
                    if metric_result.get("status") == "pass"
                    else f"Metric failed with {len(issues)} issue(s)."
                ),
                "details": {
                    "score": metric_result.get("score"),
                    "confidence": metric_result.get("confidence"),
                    "issue_types": sorted({str(issue.get("type", "unknown")) for issue in issues}),
                },
            }
        )
    return results


def _estimate_metric_severity(metric_result: Dict[str, Any]) -> int:
    if metric_result.get("status") == "pass":
        return 0

    issue_types = {str(issue.get("type", "unknown")) for issue in metric_result.get("issues", [])}
    if any(
        issue_type
        in {
            "nonexistent_package",
            "version_not_found",
            "runtime_reference_error",
            "runtime_symbol_failure",
            "sandbox_execution_failed",
            "unresolvable_import",
        }
        for issue_type in issue_types
    ):
        return 4
    if any(
        issue_type in {"invalid_flag", "invalid_subcommand", "missing_required_import"}
        for issue_type in issue_types
    ):
        return 2
    return 1


def _run_adversarial_injection_checks() -> Dict[str, Any]:
    required_metrics = {
        METRIC_DEPENDENCY,
        METRIC_API,
        METRIC_CLI,
        METRIC_EXECUTABLE,
        METRIC_REQUIREMENT,
        METRIC_RECURRENT,
    }
    cases = [
        {
            "case_id": "fake_import",
            "artifact": "import nonexistent_pkg_xyz\n",
            "prompt_spec": {"artifact_type": "code"},
            "expected_metrics": {
                METRIC_DEPENDENCY: {"issue_types": {"nonexistent_package"}},
                METRIC_EXECUTABLE: {"issue_types": {"unresolvable_import"}},
            },
        },
        {
            "case_id": "fake_api_call",
            "artifact": (
                "import requests\n\n"
                "def main():\n"
                "    return requests.get_super('https://example.com')\n"
            ),
            "prompt_spec": {"artifact_type": "code"},
            "expected_metrics": {
                METRIC_API: {"issue_types": {"invalid_api_symbol"}},
                METRIC_EXECUTABLE: {"issue_types": {"runtime_symbol_failure"}},
            },
        },
        {
            "case_id": "invalid_cli_flag",
            "artifact": "git status --ultra-fast-mode",
            "prompt_spec": {"artifact_type": "command"},
            "expected_metrics": {
                METRIC_CLI: {"issue_types": {"invalid_flag"}},
                METRIC_EXECUTABLE: {"issue_types": {"command_execution_failure"}},
            },
        },
        {
            "case_id": "requirement_mismatch",
            "artifact": "import json\nprint('ready')\n",
            "prompt_spec": {
                "artifact_type": "code",
                "contract": {"required_imports": ["requests"]},
            },
            "expected_metrics": {
                METRIC_REQUIREMENT: {"issue_types": {"missing_required_import"}},
            },
        },
        {
            "case_id": "runtime_failure",
            "artifact": "def main():\n    print(undefined_variable)\n\nmain()\n",
            "prompt_spec": {"artifact_type": "code"},
            "expected_metrics": {
                METRIC_EXECUTABLE: {
                    "issue_types": {
                        "runtime_reference_error",
                        "sandbox_execution_failed",
                    }
                },
            },
        },
        {
            "case_id": "recurrent_fake_import",
            "artifact": "import nonexistent_pkg_xyz\n",
            "prompt_spec": {"artifact_type": "code"},
            "historical_outputs": [
                {
                    "artifact": "import nonexistent_pkg_xyz\n",
                    "metadata": {"artifact_type": "code"},
                    "prompt_spec": {"artifact_type": "code"},
                }
            ],
            "expected_metrics": {
                METRIC_RECURRENT: {"issue_types": {"recurrent_hallucination"}},
            },
        },
    ]

    metric_coverage = {metric: 0 for metric in required_metrics}
    case_results: List[Dict[str, Any]] = []
    for case in cases:
        prompt_spec = case.get("prompt_spec", {})
        prepared_metadata = prepare_artifact_metadata(case["artifact"], case.get("metadata"), prompt_spec)
        failure_report = run_failure_checks(
            artifact_id=f"adversarial:{case['case_id']}",
            artifact=case["artifact"],
            metadata=prepared_metadata,
            prompt_spec=prompt_spec,
            historical_outputs=case.get("historical_outputs", []),
        )
        _assert_adversarial_failure_detected(case, failure_report)
        for metric_name in case["expected_metrics"]:
            metric_coverage[metric_name] += 1
        case_results.append(
            {
                "case_id": case["case_id"],
                "artifact": case["artifact"],
                "expected_metrics": sorted(case["expected_metrics"]),
                "failure_report": failure_report,
            }
        )

    uncovered_metrics = sorted(metric for metric, count in metric_coverage.items() if count == 0)
    if uncovered_metrics:
        raise AssertionError(
            "Adversarial injection suite does not cover all required metrics: "
            f"{', '.join(uncovered_metrics)}"
        )
    return {
        "status": "pass",
        "total_cases": len(cases),
        "metric_coverage": sorted(metric_coverage),
        "cases": case_results,
    }


def _assert_adversarial_failure_detected(case: Dict[str, Any], failure_report: Dict[str, Any]) -> None:
    if failure_report.get("overall_status") != "fail":
        raise AssertionError(f"Adversarial case '{case['case_id']}' did not fail overall.")
    metric_results = {result["metric"]: result for result in failure_report.get("metric_results", [])}
    for metric_name, expectation in case["expected_metrics"].items():
        metric_result = metric_results.get(metric_name)
        if metric_result is None:
            raise AssertionError(
                f"Adversarial case '{case['case_id']}' is missing metric result for '{metric_name}'."
            )
        if metric_result.get("status") != "fail":
            raise AssertionError(
                f"Adversarial case '{case['case_id']}' was not detected by metric '{metric_name}'."
            )
        expected_issue_types = set(expectation.get("issue_types", set()))
        if expected_issue_types:
            observed_issue_types = {str(issue.get("type", "")) for issue in metric_result.get("issues", [])}
            if not expected_issue_types & observed_issue_types:
                raise AssertionError(
                    f"Adversarial case '{case['case_id']}' failed metric '{metric_name}', "
                    f"but did not emit any of the expected issue types: {sorted(expected_issue_types)}"
                )


def _build_prompt_spec(prompt: "Prompt" | None) -> Dict[str, Any]:
    if prompt is None:
        return {}
    contract = dict(prompt.contract or {})
    prompt_spec: Dict[str, Any] = {"contract": contract}
    if prompt.language and prompt.language.lower() == "python":
        prompt_spec["artifact_type"] = "code"
    if contract.get("entrypoint") == "main_stdin":
        prompt_spec.setdefault("must_define", ["main"])
    return prompt_spec


def _build_history_record(sample: "CodeSample") -> Dict[str, Any]:
    prompt_spec: Dict[str, Any] = {"contract": sample.prompt_contract or {}}
    if sample.artifact_metadata.get("artifact_type"):
        prompt_spec["artifact_type"] = sample.artifact_metadata["artifact_type"]
    return {
        "artifact_id": f"{sample.prompt_id}:{sample.sample_id}",
        "prompt_file": sample.prompt_file,
        "prompt_file_stem": sample.prompt_file_stem,
        "run_id": sample.run_id,
        "artifact": sample.code,
        "metadata": sample.artifact_metadata,
        "prompt_spec": prompt_spec,
        "metric_results": sample.failure_report.get("metric_results", []) if sample.failure_report else [],
        "hallucination_flags": sample.failure_report.get("hallucination_flags", []) if sample.failure_report else [],
    }


def _build_model_client_config(config: "PipelineConfig") -> Dict[str, Any]:
    client_config = {
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "reasoning_effort": config.reasoning_effort,
        "retries": config.retries,
        "request_timeout": config.request_timeout,
    }
    if config.gateway_base_url:
        client_config["gateway_base_url"] = config.gateway_base_url
    if config.gateway_model_override:
        client_config["gateway_model_override"] = config.gateway_model_override
    if config.gateway_model_map_path:
        client_config["gateway_model_map_path"] = str(config.gateway_model_map_path)
    return client_config


def _build_error_record(
    *,
    prompt: "Prompt",
    model: str,
    sample_id: int,
    sample_number: int,
    run_id: int,
    prompt_file: str | None,
    prompt_file_stem: str | None,
    error: Exception,
) -> Dict[str, Any]:
    return {
        "prompt_id": prompt.prompt_id,
        "prompt": prompt.prompt,
        "prompt_file": prompt_file,
        "prompt_file_stem": prompt_file_stem,
        "model": model,
        "run_id": run_id,
        "sample_id": sample_id,
        "sample_number": sample_number,
        "status": "error",
        "error_type": type(error).__name__,
        "error_message": str(error),
        "stack_trace": traceback.format_exc(),
    }


def _sanitized_pipeline_config(config: "PipelineConfig") -> Dict[str, Any]:
    return {
        "suite_path": str(config.suite_path),
        "output_dir": str(config.output_dir),
        "model": config.model,
        "samples_per_prompt": config.samples_per_prompt,
        "runs_per_prompt": config.effective_runs_per_prompt,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "reasoning_effort": config.reasoning_effort,
        "retries": config.retries,
        "request_timeout": config.request_timeout,
        "gateway_base_url": config.gateway_base_url,
        "gateway_model_override": config.gateway_model_override,
        "gateway_model_map_path": str(config.gateway_model_map_path)
        if config.gateway_model_map_path
        else None,
        "skip_dynamic": config.skip_dynamic,
        "skip_urls": config.skip_urls,
        "save_code": config.save_code,
        "save_raw_output": config.save_raw_output,
        "run_failure_checks": config.run_failure_checks,
        "results_dir": str(config.results_dir) if config.results_dir else None,
        "evaluate_artifacts": str(config.evaluate_artifacts) if config.evaluate_artifacts else None,
        "disable_sandbox": config.disable_sandbox,
        "recurrence_threshold": config.recurrence_threshold,
        "fail_on_generation_error": config.fail_on_generation_error,
        "write_global_summary": config.write_global_summary,
    }
