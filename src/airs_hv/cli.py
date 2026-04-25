import argparse
import logging
from pathlib import Path

from .generator import (
    CANDIDATE_GATEWAY_MODELS,
    ConfigurationError,
    DEFAULT_CANDIDATE_MODELS_FILE,
    DEFAULT_RESOLVED_MODEL_MAP_FILENAME,
    discover_gateway_models,
    gateway_model_listing_guidance,
    load_gateway_model_candidates,
    list_gateway_models,
    probe_gateway_models,
    recommend_probed_gateway_models,
    resolve_model_selection,
    smoke_test_models,
    suggest_gateway_model_map,
    write_probed_gateway_model_map,
    write_gateway_model_map,
    write_resolved_gateway_model_map,
)
from .hallucination_checks import evaluate_artifact_directory, run_adversarial_self_checks
from .pipeline import run_pipeline
from .schema import PipelineConfig


def main(argv: list[str] | None = None) -> int:
    """Command-line interface for discovery, smoke tests, and full pipeline runs."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(
        description="Run the AIRS hallucination-validation pipeline with the JHU AI Gateway."
    )
    parser.add_argument(
        "--input",
        dest="suite_path",
        default=None,
        help="Path to the prompt suite JSONL file.",
    )
    parser.add_argument(
        "--model",
        dest="model",
        default=None,
        help=(
            "Model selection: a single alias like gpt-5, a comma-separated list like "
            "gpt-5,gemini-pro,claude-sonnet, or 'all'."
        ),
    )
    parser.add_argument(
        "--gateway-model",
        dest="gateway_model_override",
        default=None,
        help="Exact gateway model ID override for a single model run, for example gpt-5.",
    )
    parser.add_argument(
        "--gateway-model-map",
        dest="gateway_model_map_path",
        default=None,
        help="Optional JSON file that maps local aliases to exact gateway model IDs.",
    )
    parser.add_argument(
        "--list-gateway-models",
        dest="list_gateway_models",
        action="store_true",
        help="List available gateway model IDs and exit.",
    )
    parser.add_argument(
        "--write-model-map",
        dest="write_model_map_path",
        default=None,
        help="Write a model map JSON file from discovery or probing results.",
    )
    parser.add_argument(
        "--probe-gateway-models",
        dest="probe_gateway_models",
        action="store_true",
        help="Probe candidate gateway model IDs with real smoke-test calls.",
    )
    parser.add_argument(
        "--probe-model-alias",
        dest="probe_model_alias",
        default=None,
        help="Probe candidate gateway model IDs for one alias or 'all'.",
    )
    parser.add_argument(
        "--candidate-models-file",
        dest="candidate_models_file",
        default=DEFAULT_CANDIDATE_MODELS_FILE,
        help="JSON file containing candidate gateway model IDs to probe.",
    )
    parser.add_argument(
        "--smoke-test",
        dest="smoke_test",
        action="store_true",
        help="Run a minimal gateway call with 'Reply with exactly: OK' and exit.",
    )
    parser.add_argument(
        "--smoke-test-all",
        dest="smoke_test_all",
        action="store_true",
        help="Run smoke tests for all configured model aliases and exit.",
    )
    parser.add_argument(
        "--fix-model-map",
        dest="fix_model_map",
        action="store_true",
        help="Smoke-test all aliases, repair policy-rejected mappings with candidates, and write gateway_models.resolved.json.",
    )
    parser.add_argument(
        "--output-dir",
        "--output",
        "--out",
        dest="output_dir",
        default=None,
        help="Directory where traces, reports, and optional code artifacts are written.",
    )
    parser.add_argument(
        "--save-code",
        "--store-artifacts",
        dest="save_code",
        action="store_true",
        help="Save each raw generated artifact into the output directory.",
    )
    parser.add_argument(
        "--save-raw-output",
        dest="save_raw_output",
        action="store_true",
        help="Save the unmodified model response before Python-only validation.",
    )
    parser.add_argument(
        "--samples-per-prompt",
        dest="samples_per_prompt",
        type=int,
        default=1,
        help="Number of fresh generations per prompt. Default: 1.",
    )
    parser.add_argument(
        "--temperature",
        dest="temperature",
        type=float,
        default=None,
        help="Optional sampling temperature. If omitted, the gateway default is used.",
    )
    parser.add_argument(
        "--reasoning-effort",
        dest="reasoning_effort",
        default=None,
        help=(
            "Optional reasoning-effort override for models that support it, such as GPT-5. "
            "If omitted, model-specific defaults are used."
        ),
    )
    parser.add_argument(
        "--max-completion-tokens",
        "--max-tokens",
        dest="max_tokens",
        type=int,
        default=None,
        help="Optional completion-token override. If omitted, model-specific defaults are used.",
    )
    parser.add_argument(
        "--retries",
        dest="retries",
        type=int,
        default=3,
        help="Retry attempts for transient gateway errors. Default: 3.",
    )
    parser.add_argument(
        "--request-timeout",
        dest="request_timeout",
        type=float,
        default=60.0,
        help="Timeout in seconds for each gateway request. Default: 60.0.",
    )
    parser.add_argument(
        "--gateway-base",
        dest="gateway_base_url",
        default=None,
        help="Optional explicit gateway base URL. Defaults to GATEWAY_BASE or the known JHU base URL.",
    )
    parser.add_argument(
        "--skip-dynamic",
        dest="skip_dynamic",
        action="store_true",
        help="Skip sandbox execution checks where supported.",
    )
    parser.add_argument(
        "--skip-urls",
        dest="skip_urls",
        action="store_true",
        help="Skip URL-related validation checks where supported.",
    )
    parser.add_argument(
        "--run-failure-checks",
        dest="run_failure_checks",
        action="store_true",
        help="Write hallucination failure-check JSONL/CSV summaries.",
    )
    parser.add_argument(
        "--results-dir",
        dest="results_dir",
        default=None,
        help="Directory for failure-check outputs. Default: <output-dir>/results or ./results.",
    )
    parser.add_argument(
        "--evaluate-artifacts",
        dest="evaluate_artifacts",
        default=None,
        help="Evaluate saved .py artifacts in a directory without running generation.",
    )
    parser.add_argument(
        "--disable-sandbox",
        dest="disable_sandbox",
        action="store_true",
        help="Disable Docker execution for EIPR in the new failure-check report.",
    )
    parser.add_argument(
        "--recurrence-threshold",
        dest="recurrence_threshold",
        type=int,
        default=2,
        help="Minimum repeated invalid item count for RHSR. Default: 2.",
    )
    parser.add_argument(
        "--fail-on-generation-error",
        dest="fail_on_generation_error",
        default="false",
        choices=("true", "false"),
        help="Whether generation errors should make the full pipeline command fail. Default: false.",
    )

    args = parser.parse_args(argv)

    generator_config = {
        "temperature": args.temperature,
        "reasoning_effort": args.reasoning_effort,
        "max_tokens": args.max_tokens,
        "retries": args.retries,
        "request_timeout": args.request_timeout,
        "gateway_base_url": args.gateway_base_url,
        "gateway_model_override": args.gateway_model_override,
        "gateway_model_map_path": args.gateway_model_map_path,
    }

    if args.list_gateway_models:
        exit_code = _handle_list_gateway_models(args, generator_config)
        if not args.probe_gateway_models:
            return exit_code

    if args.probe_gateway_models:
        return _handle_probe_gateway_models(args, generator_config)

    if args.probe_model_alias:
        return _handle_probe_model_alias(args, generator_config)

    if args.fix_model_map:
        return _handle_fix_model_map(args, generator_config)

    if args.smoke_test or args.smoke_test_all:
        return _handle_smoke_tests(parser, args, generator_config)

    if args.evaluate_artifacts:
        if not args.suite_path:
            parser.error("--input is required with --evaluate-artifacts.")
        if Path(args.suite_path).suffix.lower() != ".jsonl":
            parser.error("--input must point to a JSONL file. JSON arrays are not supported.")
        if not args.run_failure_checks:
            parser.error("--evaluate-artifacts requires --run-failure-checks.")
        results_dir = Path(args.results_dir or "results")
        self_checks = run_adversarial_self_checks(
            recurrence_threshold=args.recurrence_threshold,
            disable_sandbox=False,
        )
        print(
            "Hallucination failure self-checks passed: "
            f"{self_checks['total_cases']} injected case(s)"
        )
        outputs = evaluate_artifact_directory(
            artifact_dir=Path(args.evaluate_artifacts),
            prompts_path=Path(args.suite_path),
            results_dir=results_dir,
            recurrence_threshold=args.recurrence_threshold,
            disable_sandbox=args.disable_sandbox or args.skip_dynamic,
        )
        print(f"Evaluated saved artifacts: {outputs['records_evaluated']}")
        print(f"Failure checks: {outputs['failure_checks_jsonl']}")
        print(f"Failure summary: {outputs['failure_summary_json']}")
        return 0

    if not args.model:
        parser.error("--model is required unless discovery, smoke testing, or --evaluate-artifacts is used.")
    if args.gateway_model_override and (args.model.strip().lower() == "all" or "," in args.model):
        parser.error("--gateway-model can only be used with a single model alias.")
    if not args.suite_path:
        parser.error("--input is required for a full pipeline run.")
    if not args.output_dir:
        parser.error("--output-dir is required for a full pipeline run.")

    suite_path = Path(args.suite_path)
    output_dir = Path(args.output_dir)
    if suite_path.suffix.lower() != ".jsonl":
        parser.error("--input must point to a JSONL file. JSON arrays are not supported.")

    run_pipeline(
        PipelineConfig(
            suite_path=suite_path,
            output_dir=output_dir,
            model=args.model,
            samples_per_prompt=args.samples_per_prompt,
            temperature=args.temperature,
            reasoning_effort=args.reasoning_effort,
            max_tokens=args.max_tokens,
            retries=args.retries,
            request_timeout=args.request_timeout,
            gateway_base_url=args.gateway_base_url,
            gateway_model_override=args.gateway_model_override,
            gateway_model_map_path=Path(args.gateway_model_map_path)
            if args.gateway_model_map_path
            else None,
            skip_dynamic=args.skip_dynamic,
            skip_urls=args.skip_urls,
            save_code=args.save_code,
            save_raw_output=args.save_raw_output,
            run_failure_checks=args.run_failure_checks,
            results_dir=Path(args.results_dir) if args.results_dir else None,
            evaluate_artifacts=Path(args.evaluate_artifacts) if args.evaluate_artifacts else None,
            disable_sandbox=args.disable_sandbox,
            recurrence_threshold=args.recurrence_threshold,
            fail_on_generation_error=args.fail_on_generation_error == "true",
        )
    )
    return 0


def _handle_list_gateway_models(args: argparse.Namespace, generator_config: dict[str, object]) -> int:
    try:
        discovery = discover_gateway_models(generator_config)
    except ConfigurationError as exc:
        print(exc)
        return 1

    if not discovery.models:
        if discovery.policy_enforced:
            print(
                "Gateway model listing endpoint requires a model for policy enforcement. "
                "Generic model listing is unavailable for this key. Use --probe-gateway-models "
                "or provide --gateway-model-map."
            )
            print()
            print(gateway_model_listing_guidance())
        else:
            print(
                "Could not list models from the Gateway. Check the JHU Gateway docs or pass "
                "--gateway-model explicitly."
            )
        return 0

    for model_id in discovery.models:
        print(model_id)

    suggestions = suggest_gateway_model_map(discovery.models)
    print("\nSuggested aliases to verify:")
    for alias, gateway_model in suggestions.items():
        if gateway_model.startswith("<exact-"):
            continue
        print(f"{alias}: {gateway_model}")

    if args.write_model_map_path and not args.probe_gateway_models:
        written = write_gateway_model_map(Path(args.write_model_map_path), discovery.models)
        print(f"\nWrote gateway model map to {args.write_model_map_path}")
        unresolved = [alias for alias, model_id in written.items() if model_id.startswith("<exact-")]
        if unresolved:
            print(f"Fill in unresolved aliases manually: {', '.join(unresolved)}")

    return 0


def _handle_probe_gateway_models(args: argparse.Namespace, generator_config: dict[str, object]) -> int:
    try:
        candidate_models = load_gateway_model_candidates(Path(args.candidate_models_file))
        probe_results = probe_gateway_models(candidate_models, generator_config)
    except ConfigurationError as exc:
        print(exc)
        return 1

    _print_probe_results(probe_results)

    if args.write_model_map_path:
        written = write_probed_gateway_model_map(Path(args.write_model_map_path), probe_results)
        print(f"Wrote verified gateway model map to {args.write_model_map_path}")
        unresolved = [alias for alias, model_id in written.items() if model_id is None]
        if unresolved:
            print(f"Aliases still unresolved after probing: {', '.join(unresolved)}")

    return 0


def _handle_probe_model_alias(args: argparse.Namespace, generator_config: dict[str, object]) -> int:
    try:
        aliases = resolve_model_selection(args.probe_model_alias)
        candidate_models = {
            alias: list(CANDIDATE_GATEWAY_MODELS.get(alias, []))
            for alias in aliases
        }
        probe_results = probe_gateway_models(candidate_models, generator_config)
        probe_results = {alias: probe_results.get(alias, []) for alias in aliases}
    except ConfigurationError as exc:
        print(exc)
        return 1

    _print_probe_results(probe_results)

    if args.write_model_map_path:
        written = write_probed_gateway_model_map(Path(args.write_model_map_path), probe_results)
        print(f"Wrote verified gateway model map to {args.write_model_map_path}")
        unresolved = [alias for alias, model_id in written.items() if model_id is None and alias in aliases]
        if unresolved:
            print(f"Aliases still unresolved after probing: {', '.join(unresolved)}")

    return 0


def _handle_smoke_tests(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    generator_config: dict[str, object],
) -> int:
    selected_model_spec = "all" if args.smoke_test_all else args.model
    if not selected_model_spec:
        parser.error("--smoke-test requires --model, or use --smoke-test-all.")
    if args.gateway_model_override and (
        selected_model_spec.strip().lower() == "all" or "," in selected_model_spec
    ):
        parser.error("--gateway-model can only be used with a single model alias.")

    try:
        smoke_config = dict(generator_config)
        if selected_model_spec.strip().lower() == "all":
            smoke_config["auto_repair_policy_models"] = True
        results = smoke_test_models(selected_model_spec, smoke_config)
    except ConfigurationError as exc:
        print(exc)
        return 1

    _print_smoke_test_results(results)
    passed = sum(1 for result in results if result["status"] == "pass")
    failed = len(results) - passed
    print(f"Smoke tests complete: {passed} passed, {failed} failed.")
    return 0 if failed == 0 else 1


def _handle_fix_model_map(args: argparse.Namespace, generator_config: dict[str, object]) -> int:
    output_path = Path(args.write_model_map_path or DEFAULT_RESOLVED_MODEL_MAP_FILENAME)
    try:
        fix_config = dict(generator_config)
        fix_config["auto_repair_policy_models"] = True
        results = smoke_test_models("all", fix_config)
    except ConfigurationError as exc:
        print(exc)
        return 1

    _print_smoke_test_results(results)
    passed = sum(1 for result in results if result["status"] == "pass")
    failed = len(results) - passed
    print(f"Smoke tests complete: {passed} passed, {failed} failed.")

    written = write_resolved_gateway_model_map(output_path, results)
    print(f"Wrote resolved gateway model map to {output_path}")
    unresolved = [alias for alias, gateway_model in written.items() if gateway_model is None]
    if unresolved:
        print(f"Aliases still unresolved after repair: {', '.join(unresolved)}")
    return 0 if failed == 0 else 1


def _print_smoke_test_results(results: list[dict[str, object]]) -> None:
    print("| alias | gateway_model | status | response_preview | error |")
    print("| --- | --- | --- | --- | --- |")
    for result in results:
        print(
            "| {alias} | {gateway_model} | {status} | {response_preview} | {error} |".format(
                alias=_escape_table_value(result.get("alias")),
                gateway_model=_escape_table_value(result.get("gateway_model")),
                status=_escape_table_value(result.get("status")),
                response_preview=_escape_table_value(result.get("response_preview")),
                error=_escape_table_value(result.get("error")),
            )
        )


def _print_probe_results(results: dict[str, list[dict[str, object]]]) -> None:
    recommendations = recommend_probed_gateway_models(results)
    for alias, alias_results in results.items():
        print(f"Alias: {alias}")
        print()
        print("| candidate_model | status | response_preview | error |")
        print("| --- | --- | --- | --- |")
        for result in alias_results:
            print(
                "| {candidate_model} | {status} | {response_preview} | {error} |".format(
                    candidate_model=_escape_table_value(result.get("candidate_model")),
                    status=_escape_table_value(result.get("status")),
                    response_preview=_escape_table_value(result.get("response_preview")),
                    error=_escape_table_value(result.get("error")),
                )
            )
        recommended = recommendations.get(alias)
        if recommended:
            print()
            print("Recommended mapping:")
            print(f"{alias} -> {recommended}")
        print()


def _escape_table_value(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
