import json

import pytest

import airs_hv.failure_checks as failure_checks_module
import airs_hv.pipeline as pipeline_module
from airs_hv.failure_checks import (
    METRIC_DEPENDENCY,
    check_api_validity,
    check_cli_validity,
    check_dependency_hallucination,
    check_executable_integrity,
    check_recurrent_hallucination,
    check_requirement_consistency,
    run_failure_checks,
)
from airs_hv.generator import MODEL_ALIASES, MODEL_MAP
from airs_hv.generator.base import GenerationTrace, ModelClient
from airs_hv.pipeline import run_pipeline
from airs_hv.schema import PipelineConfig


class FakeModelClient(ModelClient):
    def __init__(self, outputs, *, model_target="gpt-5"):
        self._outputs = list(outputs)
        self._model_target = model_target
        self._last_trace = None
        self._last_raw_output = None

    @property
    def provider(self) -> str:
        return "jhu-ai-gateway"

    @property
    def model_name(self) -> str:
        return self._model_target

    @property
    def model_target(self) -> str:
        return self._model_target

    @property
    def last_trace(self):
        return self._last_trace

    @property
    def last_raw_output(self):
        return self._last_raw_output

    @property
    def gateway_model_id(self) -> str:
        return f"gateway/{self._model_target}"

    def generate(self, prompt: str, *, prompt_id: str | None = None) -> str:
        if not self._outputs:
            raise AssertionError("FakeModelClient ran out of outputs.")
        code = self._outputs.pop(0)
        self._last_raw_output = code
        self._last_trace = GenerationTrace(
            provider=self.provider,
            model_name=self.model_name,
            model_target=self.model_target,
            request={
                "prompt": prompt,
                "prompt_id": prompt_id,
                "gateway_model": self.gateway_model_id,
                "request_id": f"req-{len(prompt)}",
                "prompt_chars": len(prompt),
            },
            response={"response_id": f"resp-{len(code)}", "usage": {"total_tokens": 10}},
        )
        return code


def test_failure_report_flags_hallucinated_artifacts():
    artifact = """
import requests
import imaginary_sdk

def main():
    return requests.fetch_json("https://example.com")
"""

    metadata = {
        "artifact_type": "code",
        "imports": ["requests", "imaginary_sdk", "pandas==9.9.9"],
        "api_calls": [
            {"library": "requests", "symbol": "fetch_json", "kind": "function", "args_count": 1},
        ],
        "cli_calls": ["git status --json"],
    }

    prompt_spec = {
        "artifact_type": "code",
        "required_imports": ["requests"],
        "must_define": ["main"],
    }

    history = [
        {
            "artifact": "import imaginary_sdk\nrequests.fetch_json('https://example.com')",
            "metadata": {
                "artifact_type": "code",
                "imports": ["imaginary_sdk"],
                "api_calls": [{"library": "requests", "symbol": "fetch_json"}],
            },
        }
    ]

    report = run_failure_checks(
        artifact_id="artifact-001",
        artifact=artifact,
        metadata=metadata,
        prompt_spec=prompt_spec,
        historical_outputs=history,
    )

    assert report["artifact_id"] == "artifact-001"
    assert report["overall_status"] == "fail"
    assert report["risk_score"] > 0.0
    assert any(issue["type"] == "nonexistent_package" for issue in report["hallucination_flags"])
    assert any(issue["type"] == "invalid_api_symbol" for issue in report["hallucination_flags"])
    assert any(issue["type"] == "recurrent_hallucination" for issue in report["hallucination_flags"])


def test_dependency_check_passes_for_known_imports():
    artifact = "import json\nfrom pathlib import Path\n"
    metadata = {"artifact_type": "code", "imports": ["json", "pathlib"]}

    result = check_dependency_hallucination(
        artifact=artifact,
        metadata=metadata,
        prompt_spec={},
        historical_outputs=[],
    )

    assert result["status"] == "pass"
    assert result["score"] == 1.0
    assert result["issues"] == []


def test_api_check_fails_for_unknown_library_schema():
    result = check_api_validity(
        artifact=(
            "import imaginary_sdk\n\n"
            "def main():\n"
            "    return imaginary_sdk.made_up_call()\n"
        ),
        metadata={"artifact_type": "code"},
        prompt_spec={},
        historical_outputs=[],
    )

    assert result["status"] == "fail"
    assert result["score"] == 0.0
    assert result["issues"][0]["type"] == "invalid_api_symbol"


def test_requirement_check_reads_contract_field():
    result = check_requirement_consistency(
        artifact="print('hello')",
        metadata={"artifact_type": "code"},
        prompt_spec={"contract": {"required_imports": ["requests"]}},
        historical_outputs=[],
    )

    assert result["status"] == "fail"
    assert any(issue["type"] == "missing_required_import" for issue in result["issues"])


def test_dependency_check_fails_when_code_cannot_be_parsed():
    result = check_dependency_hallucination(
        artifact="import imaginary_sdk(\n",
        metadata={"artifact_type": "code"},
        prompt_spec={},
        historical_outputs=[],
    )

    assert result["status"] == "fail"
    assert result["issues"][0]["type"] == "dependency_scan_error"


def test_cli_check_parses_raw_command_without_explicit_artifact_type():
    result = check_cli_validity(
        artifact="foocli --bad-flag",
        metadata={},
        prompt_spec={},
        historical_outputs=[],
    )

    assert result["status"] == "fail"
    assert any(issue["type"] == "cli_not_found" for issue in result["issues"])


def test_executable_integrity_fails_on_unresolved_name(monkeypatch):
    monkeypatch.setattr(
        failure_checks_module,
        "run_python_artifact_in_sandbox",
        lambda artifact, metadata=None: {
            "executed": True,
            "sandboxed": True,
            "exit_code": 1,
            "stdout": "",
            "stderr": "NameError: name 'undefined_variable' is not defined",
            "duration": 0.01,
        },
    )

    result = check_executable_integrity(
        artifact="print(undefined_variable)\n",
        metadata={"artifact_type": "code"},
        prompt_spec={},
        historical_outputs=[],
    )

    assert result["status"] == "fail"
    assert any(issue["type"] == "runtime_reference_error" for issue in result["issues"])
    assert any(issue["type"] == "sandbox_execution_failed" for issue in result["issues"])


def test_recurrent_hallucination_tracks_requirement_failures():
    artifact = "print('hello world')\n"
    metadata = {"artifact_type": "code"}
    prompt_spec = {"must_define": ["main"], "artifact_type": "code"}
    historical_outputs = [
        {
            "artifact": artifact,
            "metadata": metadata,
            "prompt_spec": prompt_spec,
        }
    ]

    result = check_recurrent_hallucination(
        artifact=artifact,
        metadata=metadata,
        prompt_spec=prompt_spec,
        historical_outputs=historical_outputs,
    )

    assert result["status"] == "fail"
    assert any(issue["type"] == "recurrent_hallucination" for issue in result["issues"])


def test_pipeline_aggregates_failure_reports_and_writes_trace(tmp_path, monkeypatch):
    monkeypatch.setattr(
        failure_checks_module,
        "run_python_artifact_in_sandbox",
        lambda artifact, metadata=None: {
            "executed": True,
            "sandboxed": True,
            "exit_code": 1,
            "stdout": "",
            "stderr": "RuntimeError: forced sandbox failure for test determinism",
            "duration": 0.01,
        },
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_model_client",
        lambda model, config=None: FakeModelClient(
            ["import imaginary_sdk\nprint(undefined_variable)\n"],
            model_target=model,
        ),
    )

    suite_path = tmp_path / "prompts.jsonl"
    output_dir = tmp_path / "out"
    prompt_record = {
        "prompt_id": "audit_prompt",
        "prompt": "Write a Python script that uses requests.",
        "family": "neutral",
        "language": "python",
        "contract": {"required_imports": ["requests"]},
    }
    suite_path.write_text(json.dumps(prompt_record) + "\n")

    report = run_pipeline(
        PipelineConfig(
            suite_path=suite_path,
            output_dir=output_dir,
            model="gpt-5",
            samples_per_prompt=1,
            save_code=True,
            save_raw_output=True,
        )
    )

    assert report["total_failures"] == 1
    assert report["aifr"] == 1.0
    assert report["models_used"] == ["gpt-5"]
    assert report["selected_models"] == ["gpt-5"]
    assert report["model_summary"]["gpt-5"]["samples"] == 1
    assert report["metric_summary"]["dependency_hallucination_rate"]["failures"] == 1
    assert report["metric_summary"]["executable_integrity_pass_rate"]["failures"] == 1
    assert report["adversarial_checks"]["status"] == "pass"
    assert report["trace_file"] == "trace.jsonl"
    assert report["report_file"] == "report.jsonl"
    assert report["total_errors"] == 0

    bundle_files = list((output_dir / "bundles").glob("*.jsonl"))
    assert len(bundle_files) == 1
    bundle = json.loads(bundle_files[0].read_text().strip())
    assert bundle["model"] == "gpt-5"
    assert bundle["artifact_file"] == "audit_prompt_gpt-5.py"
    assert bundle["meta"]["raw_output_file"] == "raw/audit_prompt_gpt-5.txt"
    assert "/" not in bundle["artifact_file"]
    assert bundle["failure_report"]["overall_status"] == "fail"
    assert bundle["meta"]["request"]["prompt_chars"] == len(prompt_record["prompt"])

    artifact_path = output_dir / bundle["artifact_file"]
    trace_path = output_dir / "trace.jsonl"
    report_path = output_dir / "report.jsonl"
    artifact_log_path = output_dir / "artifacts.jsonl"
    assert artifact_path.exists()
    assert (output_dir / "raw" / "audit_prompt_gpt-5.txt").exists()
    assert artifact_path.read_text() == "import imaginary_sdk\nprint(undefined_variable)\n"
    assert (output_dir / "raw" / "audit_prompt_gpt-5.txt").read_text() == "import imaginary_sdk\nprint(undefined_variable)\n"
    assert trace_path.exists()
    assert report_path.exists()
    assert artifact_log_path.exists()
    trace_events = [json.loads(line) for line in trace_path.read_text().splitlines()]
    assert any(event["event"] == "generation_completed" for event in trace_events)
    sample_event = next(event for event in trace_events if event["event"] == "sample_completed")
    assert sample_event["prompt"] == prompt_record["prompt"]
    assert sample_event["model"] == "gpt-5"
    assert sample_event["raw_output"] == "import imaginary_sdk\nprint(undefined_variable)\n"
    assert sample_event["generated_code_length"] == len("import imaginary_sdk\nprint(undefined_variable)\n")
    assert sample_event["evaluation_results"]["overall_status"] == "fail"
    assert sample_event["evaluation_status"] == "fail"
    assert sample_event["metrics"]["overall_status"] == "fail"
    assert sample_event["code"] == "import imaginary_sdk\nprint(undefined_variable)\n"

    report_record = json.loads(report_path.read_text().strip())
    assert report_record["model_selection"] == "gpt-5"
    artifact_record = json.loads(artifact_log_path.read_text().strip())
    assert artifact_record["artifact_file"] == "audit_prompt_gpt-5.py"
    assert artifact_record["raw_output_file"] == "raw/audit_prompt_gpt-5.txt"


def test_pipeline_flags_duplicate_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(
        failure_checks_module,
        "run_python_artifact_in_sandbox",
        lambda artifact, metadata=None: {
            "executed": True,
            "sandboxed": True,
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "duration": 0.01,
        },
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_model_client",
        lambda model, config=None: FakeModelClient(
            ["print('same output')\n", "print('same output')\n"],
            model_target=model,
        ),
    )

    suite_path = tmp_path / "prompts.jsonl"
    output_dir = tmp_path / "out"
    suite_path.write_text(
        json.dumps({"prompt_id": "p1", "prompt": "Write code A", "language": "python"}) + "\n"
        + json.dumps({"prompt_id": "p2", "prompt": "Write code B", "language": "python"}) + "\n"
    )

    report = run_pipeline(
        PipelineConfig(
            suite_path=suite_path,
            output_dir=output_dir,
            model="gpt-5",
            samples_per_prompt=1,
        )
    )

    assert report["warnings_summary"]["duplicate_output_current_run"] == 1
    bundle_files = sorted((output_dir / "bundles").glob("*.jsonl"))
    second_bundle = json.loads(bundle_files[1].read_text().strip())
    assert second_bundle["warnings"][0]["type"] == "duplicate_output_current_run"


def test_pipeline_skips_invalid_jsonl_lines_and_continues(tmp_path, monkeypatch):
    monkeypatch.setattr(
        failure_checks_module,
        "run_python_artifact_in_sandbox",
        lambda artifact, metadata=None: {
            "executed": True,
            "sandboxed": True,
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "duration": 0.01,
        },
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_model_client",
        lambda model, config=None: FakeModelClient(["print('ok')\n"], model_target=model),
    )

    suite_path = tmp_path / "prompts.jsonl"
    suite_path.write_text(
        "\n"
        + "{\"prompt_id\": \"broken\", \"prompt\": }\n"
        + json.dumps({"prompt_id": "valid", "prompt": "Write code", "language": "python"}) + "\n"
        + json.dumps({"prompt_id": "missing_prompt"}) + "\n"
    )

    report = run_pipeline(
        PipelineConfig(
            suite_path=suite_path,
            output_dir=tmp_path / "out",
            model="gpt-5",
        )
    )

    assert report["total_samples"] == 1
    assert report["prompt_load"]["loaded_prompts"] == 1
    assert report["prompt_load"]["skipped_empty_lines"] == 1
    assert report["prompt_load"]["skipped_invalid_lines"] == 2


def test_pipeline_continues_after_generation_error(tmp_path, monkeypatch):
    class FlakyModelClient(FakeModelClient):
        def generate(self, prompt: str, *, prompt_id: str | None = None) -> str:
            if prompt == "Break on this":
                raise RuntimeError("forced generation failure")
            return super().generate(prompt, prompt_id=prompt_id)

    monkeypatch.setattr(
        failure_checks_module,
        "run_python_artifact_in_sandbox",
        lambda artifact, metadata=None: {
            "executed": True,
            "sandboxed": True,
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "duration": 0.01,
        },
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_model_client",
        lambda model, config=None: FlakyModelClient(
            ["print('recovered')\n"],
            model_target=model,
        ),
    )

    suite_path = tmp_path / "prompts.jsonl"
    suite_path.write_text(
        json.dumps({"prompt_id": "p1", "prompt": "Break on this", "language": "python"}) + "\n"
        + json.dumps({"prompt_id": "p2", "prompt": "Healthy prompt", "language": "python"}) + "\n"
    )

    report = run_pipeline(
        PipelineConfig(
            suite_path=suite_path,
            output_dir=tmp_path / "out",
            model="gpt-5",
        )
    )

    assert report["total_samples"] == 1
    assert report["total_errors"] == 1
    assert report["errors"][0]["prompt_id"] == "p1"
    trace_events = [
        json.loads(line)
        for line in (tmp_path / "out" / "trace.jsonl").read_text().splitlines()
    ]
    assert any(event["event"] == "sample_error" for event in trace_events)


def test_adversarial_self_checks_raise_when_failure_is_not_detected(monkeypatch):
    monkeypatch.setattr(
        failure_checks_module,
        "run_python_artifact_in_sandbox",
        lambda artifact, metadata=None: {
            "executed": True,
            "sandboxed": True,
            "exit_code": 1,
            "stdout": "",
            "stderr": "RuntimeError: forced sandbox failure for test determinism",
            "duration": 0.01,
        },
    )

    original_run_failure_checks = pipeline_module.run_failure_checks

    def fake_run_failure_checks(*args, **kwargs):
        report = original_run_failure_checks(*args, **kwargs)
        if kwargs.get("artifact_id") == "adversarial:fake_import":
            for metric_result in report["metric_results"]:
                if metric_result["metric"] == METRIC_DEPENDENCY:
                    metric_result["status"] = "pass"
                    metric_result["score"] = 1.0
                    metric_result["issues"] = []
            report["hallucination_flags"] = [
                flag
                for flag in report["hallucination_flags"]
                if flag["metric"] != METRIC_DEPENDENCY
            ]
        return report

    monkeypatch.setattr(pipeline_module, "run_failure_checks", fake_run_failure_checks)

    with pytest.raises(
        AssertionError,
        match="Adversarial case 'fake_import' was not detected by metric 'dependency_hallucination_rate'",
    ):
        pipeline_module._run_adversarial_injection_checks()


def test_pipeline_runs_multiple_models_and_saves_per_model_artifacts(tmp_path, monkeypatch):
    monkeypatch.setitem(MODEL_ALIASES["gemini-pro"], "gateway_model", "resolved/gemini-pro")
    monkeypatch.setitem(MODEL_ALIASES["claude-sonnet"], "gateway_model", "resolved/claude-sonnet")
    monkeypatch.setattr(
        failure_checks_module,
        "run_python_artifact_in_sandbox",
        lambda artifact, metadata=None: {
            "executed": True,
            "sandboxed": True,
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "duration": 0.01,
        },
    )

    outputs_by_model = {
        "gpt-5": ["print('gpt-5 output')\n"],
        "gemini-pro": ["print('gemini output')\n"],
        "claude-sonnet": ["print('claude output')\n"],
    }

    monkeypatch.setattr(
        pipeline_module,
        "build_model_client",
        lambda model, config=None: FakeModelClient(outputs_by_model[model], model_target=model),
    )

    suite_path = tmp_path / "prompts.jsonl"
    suite_path.write_text(
        json.dumps({"prompt_id": "01", "prompt": "Write code", "language": "python"}) + "\n"
    )

    report = run_pipeline(
        PipelineConfig(
            suite_path=suite_path,
            output_dir=tmp_path / "out",
            model="gpt-5,gemini-pro,claude-sonnet",
            save_code=True,
        )
    )

    assert report["selected_models"] == ["gpt-5", "gemini-pro", "claude-sonnet"]
    assert report["total_samples"] == 3
    assert report["models_used"] == ["claude-sonnet", "gemini-pro", "gpt-5"]
    assert (tmp_path / "out" / "01_gpt-5.py").exists()
    assert (tmp_path / "out" / "01_gemini-pro.py").exists()
    assert (tmp_path / "out" / "01_claude-sonnet.py").exists()


def test_pipeline_supports_all_model_selection(tmp_path, monkeypatch):
    for alias in MODEL_ALIASES:
        monkeypatch.setitem(MODEL_ALIASES[alias], "gateway_model", f"resolved/{alias}")
    monkeypatch.setattr(
        failure_checks_module,
        "run_python_artifact_in_sandbox",
        lambda artifact, metadata=None: {
            "executed": True,
            "sandboxed": True,
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "duration": 0.01,
        },
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_model_client",
        lambda model, config=None: FakeModelClient([f"print('{model}')\n"], model_target=model),
    )

    suite_path = tmp_path / "prompts.jsonl"
    suite_path.write_text(
        json.dumps({"prompt_id": "01", "prompt": "Write code", "language": "python"}) + "\n"
    )

    report = run_pipeline(
        PipelineConfig(
            suite_path=suite_path,
            output_dir=tmp_path / "out",
            model="all",
        )
    )

    assert report["selected_models"] == list(MODEL_ALIASES)
    assert report["total_samples"] == len(MODEL_ALIASES)
