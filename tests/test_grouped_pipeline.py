import json
from pathlib import Path

import airs_hv.hallucination_checks as hc
import airs_hv.pipeline as pipeline_module
from airs_hv.generator.base import GenerationTrace, ModelClient
from airs_hv.pipeline import resolve_input_prompt_files, run_pipeline
from airs_hv.schema import PipelineConfig


class RepeatModelClient(ModelClient):
    def __init__(self, model_target="gpt-5"):
        self._model_target = model_target
        self.calls = 0
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
    def gateway_model_id(self) -> str:
        return f"gateway/{self._model_target}"

    @property
    def last_trace(self):
        return self._last_trace

    @property
    def last_raw_output(self):
        return self._last_raw_output

    def generate(self, prompt: str, *, prompt_id: str | None = None) -> str:
        self.calls += 1
        code = f"import json\nprint({self.calls!r})\n"
        self._last_raw_output = code
        self._last_trace = GenerationTrace(
            provider=self.provider,
            model_name=self.model_name,
            model_target=self.model_target,
            request={"prompt_id": prompt_id, "gateway_model": self.gateway_model_id},
            response={"usage": {"total_tokens": 1}},
        )
        return code


def _patch_fast_pipeline(monkeypatch):
    client = RepeatModelClient()
    monkeypatch.setattr(
        pipeline_module,
        "build_model_client",
        lambda model, config=None: client,
    )
    monkeypatch.setattr(
        pipeline_module,
        "_run_adversarial_injection_checks",
        lambda: {"status": "pass", "total_cases": 6},
    )
    monkeypatch.setattr(
        pipeline_module,
        "run_hallucination_adversarial_self_checks",
        lambda **kwargs: {"status": "pass", "total_cases": 6},
    )
    monkeypatch.setattr(
        pipeline_module,
        "run_failure_checks",
        lambda **kwargs: {
            "overall_status": "pass",
            "metric_results": [],
            "hallucination_flags": [],
            "risk_score": 0.0,
        },
    )
    return client


def test_resolve_input_prompt_files_accepts_file(tmp_path):
    prompt_file = tmp_path / "prompts.jsonl"
    prompt_file.write_text('{"prompt_id":"p","prompt":"Write code"}\n', encoding="utf-8")

    assert resolve_input_prompt_files(prompt_file) == [prompt_file]


def test_resolve_input_prompt_files_accepts_folder_sorted_and_skips_non_jsonl(tmp_path):
    prompt_dir = tmp_path / "prompt_sets"
    prompt_dir.mkdir()
    b_file = prompt_dir / "b_prompts.jsonl"
    a_file = prompt_dir / "a_prompts.jsonl"
    ignored = prompt_dir / "notes.txt"
    b_file.write_text('{"prompt_id":"b","prompt":"Write code"}\n', encoding="utf-8")
    a_file.write_text('{"prompt_id":"a","prompt":"Write code"}\n', encoding="utf-8")
    ignored.write_text("not jsonl", encoding="utf-8")

    assert resolve_input_prompt_files(prompt_dir) == [a_file, b_file]


def test_runs_per_prompt_writes_grouped_artifacts_raw_and_results(monkeypatch, tmp_path):
    client = _patch_fast_pipeline(monkeypatch)
    suite_path = tmp_path / "forced_failures.jsonl"
    suite_path.write_text(
        '{"prompt_id":"01_forced_fake_dependency","prompt":"Write code"}\n',
        encoding="utf-8",
    )
    output_dir = tmp_path / "outputs"
    results_dir = tmp_path / "results"

    report = run_pipeline(
        PipelineConfig(
            suite_path=suite_path,
            output_dir=output_dir,
            model="gpt-5",
            samples_per_prompt=5,
            runs_per_prompt=5,
            save_code=True,
            save_raw_output=True,
            run_failure_checks=True,
            results_dir=results_dir,
            disable_sandbox=True,
        )
    )

    artifact_dir = output_dir / "gpt-5" / "forced_failures" / "artifacts"
    raw_dir = output_dir / "gpt-5" / "forced_failures" / "raw"
    result_dir = results_dir / "gpt-5" / "forced_failures"
    failure_checks_path = result_dir / "failure_checks.jsonl"

    assert client.calls == 5
    assert report["total_samples"] == 5
    assert (artifact_dir / "01_forced_fake_dependency_run01.py").exists()
    assert (artifact_dir / "01_forced_fake_dependency_run05.py").exists()
    assert (raw_dir / "01_forced_fake_dependency_run01.txt").exists()
    assert failure_checks_path.exists()
    records = [
        json.loads(line)
        for line in failure_checks_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [record["run_id"] for record in records] == [1, 2, 3, 4, 5]
    assert all(record["prompt_file"] == "forced_failures.jsonl" for record in records)
    assert all(record["prompt_file_stem"] == "forced_failures" for record in records)
    assert all("outputs/gpt-5/forced_failures/artifacts" in record["artifact_path"] for record in records)
    assert (result_dir / "failure_summary.json").exists()
    assert (result_dir / "failure_summary_by_metric.csv").exists()
    assert not (results_dir / "global_failure_summary.json").exists()


def test_folder_input_writes_separate_dataset_summaries_and_optional_global(
    monkeypatch,
    tmp_path,
):
    _patch_fast_pipeline(monkeypatch)
    prompt_dir = tmp_path / "prompt_sets"
    prompt_dir.mkdir()
    (prompt_dir / "api_hallucinations.jsonl").write_text(
        '{"prompt_id":"api","prompt":"Write code"}\n',
        encoding="utf-8",
    )
    (prompt_dir / "forced_failures.jsonl").write_text(
        '{"prompt_id":"forced","prompt":"Write code"}\n',
        encoding="utf-8",
    )
    (prompt_dir / "ignore.md").write_text("skip me", encoding="utf-8")
    output_dir = tmp_path / "outputs"
    results_dir = tmp_path / "results"

    report = run_pipeline(
        PipelineConfig(
            suite_path=prompt_dir,
            output_dir=output_dir,
            model="gpt-5",
            samples_per_prompt=1,
            save_code=True,
            run_failure_checks=True,
            results_dir=results_dir,
            disable_sandbox=True,
            write_global_summary=True,
        )
    )

    assert report["prompt_load"]["loaded_prompts"] == 2
    assert sorted(report["prompt_load"]["files"]) == [
        "api_hallucinations.jsonl",
        "forced_failures.jsonl",
    ]
    assert (results_dir / "gpt-5" / "api_hallucinations" / "failure_summary.json").exists()
    assert (results_dir / "gpt-5" / "forced_failures" / "failure_summary.json").exists()
    assert (results_dir / "global_failure_summary.json").exists()
    assert (results_dir / "global_failure_summary_by_dataset.csv").exists()


def test_rhsr_uses_repeated_run_ids():
    records = [
        hc.evaluate_artifact(
            artifact="import repeated_fake_pkg\n",
            prompt_id="p",
            model="gpt-5",
            prompt_file="forced_failures.jsonl",
            prompt_file_stem="forced_failures",
            run_id=run_id,
            disable_sandbox=True,
        )
        for run_id in (1, 2)
    ]

    hc.apply_recurrence(records, threshold=2)

    issue = records[0]["metrics"]["RHSR"]["issues"][0]
    assert issue["item"] == "repeated_fake_pkg"
    assert issue["prompt_files"] == ["forced_failures.jsonl"]
    assert issue["run_ids"] == [1, 2]


def test_readme_documents_folder_repeated_runs_and_grouped_outputs():
    text = Path("README.md").read_text(encoding="utf-8")

    assert "--input data/prompt_sets/" in text
    assert "--runs-per-prompt 5" in text
    assert "--write-global-summary" in text
    assert "outputs/gpt-5/forced_failures/artifacts/01_forced_fake_dependency_run01.py" in text
    assert "results/gpt-5/forced_failures/failure_summary.json" in text
    assert "results/global_failure_summary_by_dataset.csv" in text
