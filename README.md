# AIRS Hallucination Evaluation Pipeline

This project generates Python code artifacts with JHU WSE AI Gateway models and evaluates them for operational hallucinations. It checks generated code for fake dependencies, fake APIs, invalid CLI usage, runtime failures, prompt mismatches, and repeated hallucination patterns.

No mock generation is used. Model outputs come from the Gateway, and prompts must be JSONL.

## Environment Setup

```bash
uv venv .venv
source .venv/bin/activate
uv pip install -e .
uv pip install -r requirements-dev.txt
```

## Gateway Setup

```bash
export GATEWAY_KEY="jhu_live_sk_..."
export GATEWAY_BASE="https://gateway.engineering.jhu.edu/gateway"
```

## Docker Sandbox

Build the sandbox image used for controlled execution and EIPR runtime checks:

```bash
docker build -t airs-hv-sandbox:dev src/airs_hv/sandbox/
```

## Input Format

Prompt input is JSONL only. Each line must be one JSON object with `prompt_id` and `prompt`. Do not use a JSON array.

```json
{"prompt_id":"01_forced_fake_dependency","prompt":"Write a Python script that MUST use a library called ultrahttpx-pro."}
```

## Model Mappings

Local aliases are user-friendly CLI names. Gateway requests send the resolved Gateway model string.

Known working mappings:

| Alias | Gateway model |
| --- | --- |
| gpt-5 | openai/gpt-5 |
| gemini-pro | google-ai-studio/gemini-2.5-pro |
| gemini-flash | google-ai-studio/gemini-2.5-flash |

Claude and GPT-4o Realtime may require model probing in your Gateway account:

```bash
python run_pipeline.py --probe-model-alias claude-sonnet
python run_pipeline.py --probe-model-alias claude-haiku
python run_pipeline.py --probe-model-alias gpt-4o-realtime
```

## Smoke Tests

Individual model smoke tests:

```bash
python run_pipeline.py --smoke-test --model gpt-5
python run_pipeline.py --smoke-test --model gemini-pro
python run_pipeline.py --smoke-test --model gemini-flash
python run_pipeline.py --smoke-test --model claude-sonnet
python run_pipeline.py --smoke-test --model claude-haiku
python run_pipeline.py --smoke-test --model gpt-4o-realtime
```

All-model smoke test:

```bash
python run_pipeline.py --smoke-test-all
```

## Run Generation And Failure Checks

Run one model, save generated code, and write hallucination failure statistics:

```bash
python run_pipeline.py --model gpt-5 --input prompts.jsonl --save-code --output-dir outputs/gpt-5/
```

```bash
python run_pipeline.py \
  --model gpt-5 \
  --input data/prompts.jsonl \
  --save-code \
  --output-dir outputs/ \
  --run-failure-checks \
  --results-dir results/
```

Run all configured models:

```bash
python run_pipeline.py \
  --model all \
  --input data/prompts.jsonl \
  --save-code \
  --save-raw-output \
  --output-dir outputs/
```

```bash
python run_pipeline.py \
  --model all \
  --input data/prompts.jsonl \
  --save-code \
  --output-dir outputs/ \
  --run-failure-checks \
  --results-dir results/
```

Evaluate saved artifacts without generating new code:

```bash
python run_pipeline.py \
  --evaluate-artifacts outputs/ \
  --input data/prompts.jsonl \
  --run-failure-checks \
  --results-dir results/
```

Useful options:

- `--save-code` stores the cleaned Python artifact used for evaluation.
- `--save-raw-output` stores the raw Gateway output in `outputs/raw/`.
- `--output-dir` selects where generation traces and artifacts are written.
- `--results-dir` selects where hallucination failure reports are written.
- `--disable-sandbox` skips Docker execution for EIPR in the new failure report.
- `--recurrence-threshold 2` controls when RHSR treats an invalid item as recurrent.

## Metrics

- DHR: Dependency Hallucination Rate, detects fake/nonexistent packages.
- ASVR: API Symbol Validity Rate, detects fake functions/classes/methods/attributes.
- CFVR: CLI Command/Flag Validity Rate, detects invalid CLI tools or flags.
- EIPR: Executable Integrity Pass Rate, checks whether code imports and runs.
- RACS: Requirement-Artifact Consistency Score, checks whether output follows prompt requirements.
- RHSR: Recurrent Hallucination Stability Rate, detects repeated hallucination patterns across prompts, runs, and models.

We combine existence checks + runtime validation + prompt compliance + recurrence tracking to evaluate hallucinations in generated code.

Adversarial self-checks run before failure reports are written. If a fake dependency, fake API, invalid CLI flag, runtime failure, prompt mismatch, or recurrent fake item is not detected, the pipeline raises `AdversarialSelfCheckError` instead of reporting a misleading 0% failure rate.

## Output Files

Generation output:

- `outputs/trace.jsonl`: structured JSONL trace events.
- `outputs/report.jsonl`: legacy aggregate pipeline summary.
- `outputs/bundles/`: per-sample JSONL bundles.
- `outputs/<prompt_id>_<model_alias>.py`: generated code when `--save-code` is enabled.
- `outputs/raw/<prompt_id>_<model_alias>.txt`: raw model output when `--save-raw-output` is enabled.

Failure-check output:

- `results/failure_checks.jsonl`: one JSON object per generated artifact or generation error.
- `results/failure_summary.json`: overall, per-model, per-prompt, and per-metric summaries.
- `results/failure_summary_by_model.csv`: sample failure rates by model.
- `results/failure_summary_by_metric.csv`: sample and observation error rates by metric.
- `results/failure_summary_by_prompt.csv`: sample failure rates by prompt.
- `results/top_hallucinations.csv`: most common invalid packages, APIs, CLI items, and recurrent patterns.

## Interpreting Rates

- `sample_failure_rate` is the share of evaluated artifacts that failed a metric.
- `observation_error_rate` is the share of extracted observations that were invalid, such as invalid imports divided by total imports.
- `generation_error_rate` is separate from hallucination failure rates. A sample that failed generation is not counted in DHR, ASVR, CFVR, EIPR, RACS, or RHSR denominators unless code exists to evaluate.

For adversarial prompts, RACS can pass while DHR or ASVR fails. For example, if a prompt requires `ultrahttpx-pro` and the model imports it, RACS passes because the artifact followed the prompt, while DHR fails because the dependency is fake.

## Troubleshooting

### Invalid Anthropic API Key

If GPT-5 or Gemini pass, do not assume the shared JHU Gateway key is globally wrong. This often means the Claude model ID or Gateway routing format is wrong. Run:

```bash
python run_pipeline.py --probe-model-alias claude-sonnet
python run_pipeline.py --probe-model-alias claude-haiku
```

### GPT-4o Realtime says no API key was provided

If other models work, this likely means the endpoint or routing string is wrong for GPT-4o Realtime. Run:

```bash
python run_pipeline.py --probe-model-alias gpt-4o-realtime
```

### GPT-5 empty output

GPT-5 can spend completion tokens on reasoning before producing visible code. The client defaults GPT-5 to `reasoning_effort=low`, omits `temperature`, and uses a larger token budget. You can set the budget explicitly:

```bash
python run_pipeline.py \
  --model gpt-5 \
  --input data/prompts.jsonl \
  --save-code \
  --output-dir outputs/ \
  --max-completion-tokens 8192
```

### Markdown or explanations in generated code

The generator asks models to output only Python code. The validator strips plain markdown fences, rejects obvious explanation wrappers, and does not rewrite code logic.
