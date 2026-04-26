# AIRS Hallucination Evaluation Pipeline

This project generates Python code artifacts with JHU WSE AI Gateway models and evaluates them for operational hallucinations. The pipeline can read one JSONL file or a folder of JSONL files, run each prompt multiple times, save artifacts by model and dataset, run hallucination failure checks, and write per-metric failure summaries.

No mock generation is used. Model outputs come from the Gateway, and prompt input must be JSONL.

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

Prompt input is JSONL only. Each line must be one JSON object with `prompt_id` and `prompt`.

```json
{"prompt_id":"01_forced_fake_dependency","prompt":"Write a Python script that MUST use a library called ultrahttpx-pro."}
```

Rules:

- One JSON object per line.
- Empty lines are skipped.
- JSON arrays are not supported.
- `--input` can be a single `.jsonl` file or a folder containing multiple `.jsonl` files.
- Folder input skips non-JSONL files and sorts JSONL files alphabetically.

## Models

Supported aliases:

- `gpt-5`
- `gemini-pro`
- `gemini-flash`
- `claude-sonnet`
- `claude-haiku`
- `gpt-4o-realtime`
- `gpt-4o`
- `chatgpt-4o-latest`
- `all`

Known working mappings:

| Local alias | Gateway model ID |
| --- | --- |
| gpt-5 | openai/gpt-5 |
| gemini-pro | google-ai-studio/gemini-2.5-pro |
| gemini-flash | google-ai-studio/gemini-2.5-flash |
| claude-sonnet | anthropic/claude-sonnet-4 |
| claude-haiku | anthropic/claude-haiku-4.5 |
| gpt-4o-realtime | openai/chatgpt-4o-latest |

The aliases `gpt-4o` and `chatgpt-4o-latest` are accepted as shortcuts for `gpt-4o-realtime`. Model probing is still available if Gateway IDs change later:

```bash
python run_pipeline.py --probe-model-alias claude-sonnet
python run_pipeline.py --probe-model-alias claude-haiku
python run_pipeline.py --probe-model-alias gpt-4o-realtime
```

## Smoke Tests

```bash
python run_pipeline.py --smoke-test --model gpt-5
python run_pipeline.py --smoke-test --model gemini-pro
python run_pipeline.py --smoke-test --model gemini-flash
python run_pipeline.py --smoke-test --model claude-sonnet
python run_pipeline.py --smoke-test --model claude-haiku
python run_pipeline.py --smoke-test --model gpt-4o-realtime
python run_pipeline.py --smoke-test-all
```

## Basic Commands

Single file, one run:

```bash
python run_pipeline.py \
  --model gpt-5 \
  --input data/prompts.jsonl \
  --save-code \
  --output-dir outputs/ \
  --run-failure-checks \
  --results-dir results/
```

The older single-file generation command still works:

```bash
python run_pipeline.py --model gpt-5 --input prompts.jsonl --save-code --output-dir outputs/gpt-5/
```

Individual generation runs for Claude and GPT-4o:

```bash
python run_pipeline.py \
  --model claude-sonnet \
  --input data/prompts.jsonl \
  --save-code \
  --output-dir outputs/ \
  --run-failure-checks \
  --results-dir results/

python run_pipeline.py \
  --model claude-haiku \
  --input data/prompts.jsonl \
  --save-code \
  --output-dir outputs/ \
  --run-failure-checks \
  --results-dir results/

python run_pipeline.py \
  --model gpt-4o-realtime \
  --input data/prompts.jsonl \
  --save-code \
  --output-dir outputs/ \
  --run-failure-checks \
  --results-dir results/
```

Folder input, one model:

```bash
python run_pipeline.py \
  --model gpt-5 \
  --input data/prompt_sets/ \
  --save-code \
  --output-dir outputs/ \
  --run-failure-checks \
  --results-dir results/
```

## Generation Token Limits

The pipeline uses model-specific default `max_completion_tokens` for generation. Defaults were increased modestly, by no more than 50%, to reduce truncated code while still asking models to produce concise Python.

Current generation defaults:

```text
gpt-5: 12288
gemini-pro: 6144
gemini-flash: 6144
claude-sonnet: 6144
claude-haiku: 6144
gpt-4o-realtime: 6144
```

Higher token limits can reduce truncation, but may increase latency and cost. You can still override the default:

```bash
python run_pipeline.py \
  --model gpt-5 \
  --input data/prompts.jsonl \
  --save-code \
  --output-dir outputs/ \
  --run-failure-checks \
  --results-dir results/ \
  --max-completion-tokens 12288
```

## Multiple Runs Per Prompt

Repeated runs call the model independently for each prompt. This helps measure output instability and gives RHSR enough samples to detect recurrent hallucinations.

```bash
python run_pipeline.py \
  --model gpt-5 \
  --input data/prompt_sets/ \
  --runs-per-prompt 5 \
  --save-code \
  --save-raw-output \
  --output-dir outputs/ \
  --run-failure-checks \
  --results-dir results/
```

`--runs-per-prompt` defaults to `1`. `--samples-per-prompt` remains supported as a backward-compatible alias.

## Folder Input With All Models

Folder input, all models, five runs:

```bash
python run_pipeline.py \
  --model all \
  --input data/prompt_sets/ \
  --runs-per-prompt 5 \
  --save-code \
  --save-raw-output \
  --output-dir outputs/ \
  --run-failure-checks \
  --results-dir results/
```

With optional global summary:

```bash
python run_pipeline.py \
  --model all \
  --input data/prompt_sets/ \
  --runs-per-prompt 5 \
  --save-code \
  --save-raw-output \
  --output-dir outputs/ \
  --run-failure-checks \
  --results-dir results/ \
  --write-global-summary
```

Legacy all-model generation without failure summaries also still works:

```bash
python run_pipeline.py \
  --model all \
  --input data/prompts.jsonl \
  --save-code \
  --save-raw-output \
  --output-dir outputs/
```

## Output Structure

Generated artifacts are grouped by model alias and JSONL filename stem:

```text
outputs/
  <model_alias>/
    <jsonl_file_stem>/
      artifacts/
        <prompt_id>_run01.py
      raw/
        <prompt_id>_run01.txt
```

Failure-check results are grouped the same way:

```text
results/
  <model_alias>/
    <jsonl_file_stem>/
      failure_checks.jsonl
      failure_summary.json
      failure_summary_by_metric.csv
      failure_summary_by_prompt.csv
      top_hallucinations.csv
```

For a single grouped dataset this means paths such as `results/gpt-5/forced_failures/failure_checks.jsonl`. Older flat evaluations may still write `results/failure_checks.jsonl`.

Example:

```text
outputs/gpt-5/forced_failures/artifacts/01_forced_fake_dependency_run01.py
results/gpt-5/forced_failures/failure_summary.json
```

If `--write-global-summary` is supplied, these aggregate files are also written:

```text
results/global_failure_summary.json
results/global_failure_summary_by_model.csv
results/global_failure_summary_by_dataset.csv
results/global_failure_summary_by_metric.csv
```

## Metrics

- DHR: Dependency Hallucination Rate, detects fake/nonexistent packages.
- ASVR: API Symbol Validity Rate, detects fake functions/classes/methods/attributes.
- CFVR: CLI Command/Flag Validity Rate, detects invalid CLI tools or flags.
- EIPR: Executable Integrity Pass Rate, checks whether code imports and runs.
- RACS: Requirement-Artifact Consistency Score, checks whether output follows prompt requirements.
- RHSR: Recurrent Hallucination Stability Rate, detects repeated hallucination patterns.

We combine existence checks + runtime validation + prompt compliance + recurrence tracking to evaluate hallucinations in generated code.

Rate meanings:

- `sample_failure_rate` is the share of evaluated artifacts that failed a metric.
- `observation_error_rate` is the share of extracted observations that were invalid, such as invalid imports divided by total imports.
- `generation_error_rate` is separate from hallucination failure rates. A sample that failed generation is not counted in DHR, ASVR, CFVR, EIPR, RACS, or RHSR denominators unless code exists to evaluate.
- Repeated runs improve RHSR because the same invalid package, API, CLI flag, runtime error, or requirement violation can be observed across runs.

For adversarial prompts, RACS can pass while DHR or ASVR fails. If a prompt requires `ultrahttpx-pro` and the model imports it, RACS passes because the artifact followed the prompt, while DHR fails because the dependency is fake.

Adversarial self-checks run before failure reports are written. If a fake dependency, fake API, invalid CLI flag, runtime failure, prompt mismatch, or recurrent fake item is not detected, the pipeline raises `AdversarialSelfCheckError` instead of reporting a misleading 0% failure rate.

## Evaluating Saved Artifacts

Evaluate all artifacts under `outputs/`:

```bash
python run_pipeline.py \
  --evaluate-artifacts outputs/ \
  --input data/prompt_sets/ \
  --run-failure-checks \
  --results-dir results/
```

Evaluate one model/dataset artifact folder:

```bash
python run_pipeline.py \
  --evaluate-artifacts outputs/gpt-5/forced_failures/artifacts/ \
  --input data/prompt_sets/forced_failures.jsonl \
  --run-failure-checks \
  --results-dir results/
```

The evaluator infers model alias, dataset name, `prompt_id`, and `run_id` from the grouped folder layout and artifact filenames when possible.

## Useful Options

- `--save-code` stores the cleaned Python artifact used for evaluation.
- `--save-raw-output` stores the raw Gateway output under `outputs/<model>/<dataset>/raw/`.
- `--output-dir` selects where generation traces and artifacts are written.
- `--results-dir` selects where hallucination failure reports are written.
- `--disable-sandbox` skips Docker execution for EIPR in the new failure report.
- `--recurrence-threshold 2` controls when RHSR treats an invalid item as recurrent.
- `--write-global-summary` writes aggregate results across all models and datasets.
- `--max-completion-tokens 8192` can be useful for GPT-5 if visible output is too short.

## Troubleshooting

### Input folder has no JSONL files

Check that files end with `.jsonl`. Non-JSONL files are intentionally skipped.

### Repeated runs produce many artifacts

Total artifacts are roughly `prompt_files * prompts * models * runs_per_prompt`. Use a dedicated `--output-dir` and `--results-dir` for large experiments.

### JSONL line skipped

Each line must be a JSON object with non-empty `prompt_id` and `prompt`. Empty lines are skipped; invalid JSON lines are logged and skipped.

### Missing result summary

Summaries are only written when `--run-failure-checks` is supplied. Per-dataset summaries are under `results/<model>/<dataset>/`.

### RHSR does not fail

RHSR requires the same invalid item to appear at least `--recurrence-threshold` times. Use repeated runs or multiple prompts with shared hallucination triggers.

### Sandbox image missing

Build the image:

```bash
docker build -t airs-hv-sandbox:dev src/airs_hv/sandbox/
```

Or use `--disable-sandbox` when you need static-only failure summaries.

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

GPT-5 can spend completion tokens on reasoning before producing visible code. The client defaults GPT-5 to `reasoning_effort=low`, omits `temperature`, and uses a larger token budget:

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
