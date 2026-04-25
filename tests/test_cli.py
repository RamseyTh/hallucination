from pathlib import Path

import json

import airs_hv.cli as cli_module


def test_smoke_test_all_prints_one_table_and_calls_once(monkeypatch, capsys):
    calls = {"count": 0}

    results = [
        {
            "alias": "gpt-5",
            "gateway_model": "openai/gpt-5",
            "status": "pass",
            "response_preview": "OK",
            "error": "",
            "raw_text": "OK",
            "matched_ok": True,
        },
        {
            "alias": "gemini-pro",
            "gateway_model": "google-ai-studio/gemini-2.5-pro",
            "status": "pass",
            "response_preview": "OK",
            "error": "",
            "raw_text": "OK",
            "matched_ok": True,
        },
        {
            "alias": "gemini-flash",
            "gateway_model": "google-ai-studio/gemini-2.5-flash",
            "status": "pass",
            "response_preview": "OK",
            "error": "",
            "raw_text": "OK",
            "matched_ok": True,
        },
        {
            "alias": "claude-sonnet",
            "gateway_model": "anthropic/claude-4-sonnet-20250522",
            "status": "pass",
            "response_preview": "OK",
            "error": "",
            "raw_text": "OK",
            "matched_ok": True,
        },
        {
            "alias": "claude-haiku",
            "gateway_model": "anthropic/claude-4.5-haiku-20251001",
            "status": "pass",
            "response_preview": "OK",
            "error": "",
            "raw_text": "OK",
            "matched_ok": True,
        },
        {
            "alias": "gpt-4o-realtime",
            "gateway_model": "openai/gpt-4o-realtime",
            "status": "pass",
            "response_preview": "OK",
            "error": "",
            "raw_text": "OK",
            "matched_ok": True,
        },
    ]

    def fake_smoke_test_models(model_spec, config):
        calls["count"] += 1
        assert model_spec == "all"
        assert config["auto_repair_policy_models"] is True
        return results

    monkeypatch.setattr(cli_module, "smoke_test_models", fake_smoke_test_models)

    exit_code = cli_module.main(["--smoke-test-all"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert calls["count"] == 1
    assert output.count("| alias | gateway_model | status | response_preview | error |") == 1
    assert output.count("Smoke tests complete: 6 passed, 0 failed.") == 1
    assert "openai/gpt-5" in output


def test_smoke_test_all_with_failure_returns_nonzero(monkeypatch):
    monkeypatch.setattr(
        cli_module,
        "smoke_test_models",
        lambda model_spec, config: [
            {
                "alias": "gpt-5",
                "gateway_model": "openai/gpt-5",
                "status": "pass",
                "response_preview": "OK",
                "error": "",
                "raw_text": "OK",
                "matched_ok": True,
            },
            {
                "alias": "claude-sonnet",
                "gateway_model": "claude-4-sonnet-20250522",
                "status": "error",
                "response_preview": "",
                "error": "MODEL_REQUIRED_FOR_POLICY_ENFORCEMENT",
                "raw_text": "",
                "matched_ok": False,
            },
        ],
    )

    assert cli_module.main(["--smoke-test-all"]) == 1


def test_fix_model_map_writes_resolved_file(monkeypatch, capsys, tmp_path):
    target = tmp_path / "gateway_models.resolved.json"
    results = [
        {
            "alias": "gpt-5",
            "gateway_model": "openai/gpt-5",
            "status": "pass",
            "response_preview": "OK",
            "error": "",
            "raw_text": "OK",
            "matched_ok": True,
        },
        {
            "alias": "gemini-pro",
            "gateway_model": "google-ai-studio/gemini-2.5-pro",
            "status": "pass",
            "response_preview": "OK",
            "error": "",
            "raw_text": "OK",
            "matched_ok": True,
        },
        {
            "alias": "gemini-flash",
            "gateway_model": "google-ai-studio/gemini-2.5-flash",
            "status": "pass",
            "response_preview": "OK",
            "error": "",
            "raw_text": "OK",
            "matched_ok": True,
        },
        {
            "alias": "claude-sonnet",
            "gateway_model": "anthropic/claude-4-sonnet-20250522",
            "status": "pass",
            "response_preview": "OK",
            "error": "",
            "raw_text": "OK",
            "matched_ok": True,
        },
        {
            "alias": "claude-haiku",
            "gateway_model": "anthropic/claude-4.5-haiku-20251001",
            "status": "pass",
            "response_preview": "OK",
            "error": "",
            "raw_text": "OK",
            "matched_ok": True,
        },
        {
            "alias": "gpt-4o-realtime",
            "gateway_model": "openai/gpt-4o-realtime",
            "status": "pass",
            "response_preview": "OK",
            "error": "",
            "raw_text": "OK",
            "matched_ok": True,
        },
    ]

    monkeypatch.setattr(cli_module, "smoke_test_models", lambda model_spec, config: results)

    exit_code = cli_module.main(["--fix-model-map", "--write-model-map", str(target)])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert target.exists()
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["gpt-5"] == "openai/gpt-5"
    assert written["claude-sonnet"] == "anthropic/claude-4-sonnet-20250522"
    assert "Wrote resolved gateway model map" in output


def test_full_run_accepts_gateway_model_map_and_save_code(monkeypatch, tmp_path):
    suite_path = tmp_path / "prompts.jsonl"
    suite_path.write_text('{"prompt_id":"01","prompt":"Write code"}\n', encoding="utf-8")
    output_dir = tmp_path / "out"
    model_map = tmp_path / "gateway_models.resolved.json"
    model_map.write_text(
        json.dumps({"gpt-5": "openai/gpt-5", "claude-sonnet": "anthropic/claude-4-sonnet-20250522"}) + "\n",
        encoding="utf-8",
    )
    captured = {}

    def fake_run_pipeline(config):
        captured["model"] = config.model
        captured["suite_path"] = config.suite_path
        captured["output_dir"] = config.output_dir
        captured["save_code"] = config.save_code
        captured["save_raw_output"] = config.save_raw_output
        captured["gateway_model_map_path"] = config.gateway_model_map_path
        return {}

    monkeypatch.setattr(cli_module, "run_pipeline", fake_run_pipeline)

    exit_code = cli_module.main(
        [
            "--model",
            "all",
            "--input",
            str(suite_path),
            "--output-dir",
            str(output_dir),
            "--gateway-model-map",
            str(model_map),
            "--save-code",
            "--save-raw-output",
        ]
    )

    assert exit_code == 0
    assert captured["model"] == "all"
    assert captured["suite_path"] == suite_path
    assert captured["output_dir"] == output_dir
    assert captured["save_code"] is True
    assert captured["save_raw_output"] is True
    assert captured["gateway_model_map_path"] == Path(model_map)


def test_full_run_passes_failure_check_options(monkeypatch, tmp_path):
    suite_path = tmp_path / "prompts.jsonl"
    suite_path.write_text('{"prompt_id":"01","prompt":"Write code"}\n', encoding="utf-8")
    output_dir = tmp_path / "out"
    results_dir = tmp_path / "results"
    captured = {}

    def fake_run_pipeline(config):
        captured["run_failure_checks"] = config.run_failure_checks
        captured["results_dir"] = config.results_dir
        captured["disable_sandbox"] = config.disable_sandbox
        captured["recurrence_threshold"] = config.recurrence_threshold
        captured["fail_on_generation_error"] = config.fail_on_generation_error
        return {}

    monkeypatch.setattr(cli_module, "run_pipeline", fake_run_pipeline)

    exit_code = cli_module.main(
        [
            "--model",
            "gpt-5",
            "--input",
            str(suite_path),
            "--output-dir",
            str(output_dir),
            "--run-failure-checks",
            "--results-dir",
            str(results_dir),
            "--disable-sandbox",
            "--recurrence-threshold",
            "3",
            "--fail-on-generation-error",
            "true",
        ]
    )

    assert exit_code == 0
    assert captured["run_failure_checks"] is True
    assert captured["results_dir"] == results_dir
    assert captured["disable_sandbox"] is True
    assert captured["recurrence_threshold"] == 3
    assert captured["fail_on_generation_error"] is True


def test_evaluate_artifacts_cli_writes_results(monkeypatch, tmp_path, capsys):
    artifact_dir = tmp_path / "outputs"
    artifact_dir.mkdir()
    (artifact_dir / "01_gpt-5.py").write_text("import json\n", encoding="utf-8")
    suite_path = tmp_path / "prompts.jsonl"
    suite_path.write_text('{"prompt_id":"01","prompt":"Write code"}\n', encoding="utf-8")
    results_dir = tmp_path / "results"

    monkeypatch.setattr(
        cli_module,
        "run_adversarial_self_checks",
        lambda **kwargs: {"status": "pass", "total_cases": 6},
    )

    exit_code = cli_module.main(
        [
            "--evaluate-artifacts",
            str(artifact_dir),
            "--input",
            str(suite_path),
            "--run-failure-checks",
            "--results-dir",
            str(results_dir),
            "--disable-sandbox",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Evaluated saved artifacts: 1" in output
    assert (results_dir / "failure_checks.jsonl").exists()


def test_probe_model_alias_uses_builtin_candidates(monkeypatch, capsys):
    captured = {}

    def fake_probe_gateway_models(candidate_models, config):
        captured["candidate_models"] = candidate_models
        return {
            "claude-sonnet": [
                {
                    "alias": "claude-sonnet",
                    "candidate_model": "claude-4-sonnet-20250522",
                    "status": "model_required_for_policy",
                    "response_preview": "",
                    "error": "MODEL_REQUIRED_FOR_POLICY_ENFORCEMENT",
                },
                {
                    "alias": "claude-sonnet",
                    "candidate_model": "anthropic/claude-4-sonnet-20250522",
                    "status": "upstream_provider_auth_error",
                    "response_preview": "",
                    "error": "Invalid Anthropic API Key",
                },
            ]
        }

    monkeypatch.setattr(cli_module, "probe_gateway_models", fake_probe_gateway_models)

    exit_code = cli_module.main(["--probe-model-alias", "claude-sonnet"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert captured["candidate_models"]["claude-sonnet"][0] == "claude-4-sonnet-20250522"
    assert "Alias: claude-sonnet" in output
    assert "upstream_provider_auth_error" in output
