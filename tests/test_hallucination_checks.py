import json

import pytest

import airs_hv.hallucination_checks as hc


def test_dhr_detects_nonexistent_import():
    record = hc.evaluate_artifact(
        artifact="import nonexistent_pkg_xyz\n",
        prompt_id="p1",
        model="gpt-5",
        disable_sandbox=True,
    )

    assert record["metrics"]["DHR"]["sample_failed"] is True
    assert record["metrics"]["DHR"]["invalid_count"] == 1
    assert record["metrics"]["DHR"]["issues"][0]["name"] == "nonexistent_pkg_xyz"


def test_dhr_does_not_flag_stdlib_imports():
    record = hc.evaluate_artifact(
        artifact="import json\nfrom pathlib import Path\n",
        prompt_id="p1",
        model="gpt-5",
        disable_sandbox=True,
    )

    assert record["metrics"]["DHR"]["sample_failed"] is False
    assert record["metrics"]["DHR"]["invalid_count"] == 0


def test_asvr_detects_fake_method_call():
    record = hc.evaluate_artifact(
        artifact="import requests\nrequests.get_secure_v5('https://example.com')\n",
        prompt_id="p2",
        model="gpt-5",
        disable_sandbox=True,
    )

    asvr = record["metrics"]["ASVR"]
    assert asvr["sample_failed"] is True
    assert asvr["issues"][0]["symbol"] == "requests.get_secure_v5"


def test_cfvr_detects_fake_cli_flag():
    record = hc.evaluate_artifact(
        artifact="import subprocess\nsubprocess.run(['curl', '--ultra-speed', 'https://example.com'])\n",
        prompt_id="p3",
        model="gpt-5",
        disable_sandbox=True,
    )

    cfvr = record["metrics"]["CFVR"]
    assert cfvr["sample_failed"] is True
    assert any(issue["flag"] == "--ultra-speed" for issue in cfvr["issues"])


def test_eipr_fails_on_module_not_found(monkeypatch):
    monkeypatch.setattr(
        hc,
        "run_python_artifact_in_sandbox",
        lambda artifact, metadata=None: {
            "executed": True,
            "sandboxed": True,
            "exit_code": 1,
            "stdout": "",
            "stderr": "ModuleNotFoundError: No module named 'nonexistent_pkg_xyz'",
            "duration": 0.01,
        },
    )

    record = hc.evaluate_artifact(
        artifact="import nonexistent_pkg_xyz\n",
        prompt_id="p4",
        model="gpt-5",
    )

    eipr = record["metrics"]["EIPR"]
    assert eipr["sample_failed"] is True
    assert eipr["runtime_error_type"] == "ModuleNotFoundError"


def test_racs_detects_missing_required_library():
    record = hc.evaluate_artifact(
        artifact="print('hello')\n",
        prompt_id="p5",
        model="gpt-5",
        prompt="Write Python code that must use requests.",
        disable_sandbox=True,
    )

    assert record["metrics"]["RACS"]["sample_failed"] is True
    assert record["metrics"]["RACS"]["issues"][0]["requirement"] == "must use library requests"


def test_racs_can_pass_when_dhr_fails_for_prompt_required_fake_dependency():
    record = hc.evaluate_artifact(
        artifact="import ultrahttpx_pro\n",
        prompt_id="p6",
        model="gpt-5",
        prompt="Write Python code that MUST use a library called ultrahttpx-pro.",
        disable_sandbox=True,
    )

    assert record["metrics"]["DHR"]["sample_failed"] is True
    assert record["metrics"]["RACS"]["sample_failed"] is False


def test_rhsr_detects_repeated_fake_items():
    records = [
        hc.evaluate_artifact(
            artifact="import repeated_fake_pkg\n",
            prompt_id=f"p{idx}",
            model="gpt-5",
            disable_sandbox=True,
        )
        for idx in range(2)
    ]

    hc.apply_recurrence(records, threshold=2)

    assert all(record["metrics"]["RHSR"]["sample_failed"] for record in records)
    assert records[0]["metrics"]["RHSR"]["issues"][0]["item"] == "repeated_fake_pkg"


def test_generation_errors_are_excluded_from_metric_denominators():
    record = hc.evaluate_artifact(
        artifact="import nonexistent_pkg_xyz\n",
        prompt_id="p7",
        model="gpt-5",
        disable_sandbox=True,
    )
    summary = hc.build_failure_summary(
        records=[record],
        generation_errors=[{"prompt_id": "p8", "model": "gpt-5", "error_type": "GenerationError"}],
    )

    assert summary["generation"]["attempted"] == 2
    assert summary["generation"]["failed"] == 1
    assert summary["metrics"]["DHR"]["samples_evaluated"] == 1


def test_aggregate_stats_compute_sample_and_observation_rates():
    records = [
        hc.evaluate_artifact(
            artifact="import nonexistent_pkg_xyz\nimport json\n",
            prompt_id="p9",
            model="gpt-5",
            disable_sandbox=True,
        ),
        hc.evaluate_artifact(
            artifact="import json\n",
            prompt_id="p10",
            model="gpt-5",
            disable_sandbox=True,
        ),
    ]
    summary = hc.build_failure_summary(records=records, generation_errors=[])

    dhr = summary["metrics"]["DHR"]
    assert dhr["sample_failure_rate"] == 0.5
    assert dhr["observation_error_rate"] == pytest.approx(1 / 3)


def test_results_files_are_written(tmp_path):
    record = hc.evaluate_artifact(
        artifact="import nonexistent_pkg_xyz\n",
        prompt_id="p11",
        model="gpt-5",
        disable_sandbox=True,
    )
    hc.apply_recurrence([record], threshold=2)
    outputs = hc.write_failure_outputs(
        records=[record],
        generation_errors=[],
        results_dir=tmp_path,
    )

    assert (tmp_path / "failure_checks.jsonl").exists()
    assert (tmp_path / "failure_summary.json").exists()
    assert (tmp_path / "failure_summary_by_model.csv").exists()
    assert (tmp_path / "failure_summary_by_metric.csv").exists()
    assert (tmp_path / "failure_summary_by_prompt.csv").exists()
    assert (tmp_path / "top_hallucinations.csv").exists()
    first = json.loads((tmp_path / "failure_checks.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert first["prompt_id"] == "p11"
    assert outputs["records_evaluated"] == 1


def test_adversarial_self_checks_fail_if_detector_is_disabled(monkeypatch):
    original = hc.check_dhr

    def always_pass(contract):
        result = original(contract)
        result["sample_failed"] = False
        result["invalid_count"] = 0
        result["issues"] = []
        return result

    monkeypatch.setattr(hc, "check_dhr", always_pass)

    with pytest.raises(hc.AdversarialSelfCheckError):
        hc.run_adversarial_self_checks()


def test_readme_documents_failure_check_commands_and_metrics():
    text = open("README.md", encoding="utf-8").read()

    assert "--run-failure-checks" in text
    assert "--evaluate-artifacts outputs/" in text
    assert "results/failure_checks.jsonl" in text
    assert "DHR: Dependency Hallucination Rate" in text
    assert "ASVR: API Symbol Validity Rate" in text
    assert "CFVR: CLI Command/Flag Validity Rate" in text
    assert "EIPR: Executable Integrity Pass Rate" in text
    assert "RACS: Requirement-Artifact Consistency Score" in text
    assert "RHSR: Recurrent Hallucination Stability Rate" in text
