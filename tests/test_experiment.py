import json

import airs_hv.experiment as experiment_module


def test_run_experiment_writes_per_model_comparison(tmp_path, monkeypatch):
    def fake_run_pipeline(config):
        model_dir = config.output_dir
        model_dir.mkdir(parents=True, exist_ok=True)
        report = {
            "total_samples": 1,
            "total_failures": 1,
            "aifr": 1.0,
            "avg_risk_score": 0.5,
            "metric_summary": {
                "dependency_hallucination_rate": {
                    "samples": 1,
                    "failures": 1,
                    "failure_rate": 1.0,
                    "average_score": 0.0,
                }
            },
            "warnings_summary": {},
        }
        (model_dir / "report.jsonl").write_text(json.dumps(report) + "\n")
        return report

    monkeypatch.setattr(experiment_module, "run_pipeline", fake_run_pipeline)

    comparison = experiment_module.run_experiment(
        suite_path=tmp_path / "prompts.jsonl",
        models=["gpt-5", "gemini-flash"],
        output_dir=tmp_path / "experiment_out",
    )

    assert comparison["models"] == ["gpt-5", "gemini-flash"]
    assert set(comparison["per_model_comparison"]) == {
        "gpt-5",
        "gemini-flash",
    }
    assert (tmp_path / "experiment_out" / "comparison.jsonl").exists()
