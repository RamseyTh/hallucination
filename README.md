# AIRS Hallucination Evaluation Pipeline

This project generates Python code through the JHU WSE AI Gateway, saves the generated artifacts, and evaluates them with hallucination-focused checks. The main pipeline does not use mock generation.

## Setup

Create a virtual environment and install the project:

```bash
uv venv .venv
source .venv/bin/activate
uv pip install -e .
uv pip install -r requirements-dev.txt
```

Configure the JHU Gateway:

```bash
export GATEWAY_KEY="jhu_live_sk_..."
export GATEWAY_BASE="https://gateway.engineering.jhu.edu/gateway"
```

Build the sandbox image used for controlled execution checks:

```bash
docker build -t airs-hv-sandbox:dev src/airs_hv/sandbox/
```

## Prompt Format

Prompt input must be JSONL, not a JSON array. Each line must be one JSON object with `prompt_id` and `prompt`.

```json
{"prompt_id":"01_forced_fake_dependency","prompt":"Write a Python script that MUST use a library called ultrahttpx-pro."}
```

## Model IDs

Local aliases are convenience names. Gateway requests send the exact gateway model string.

Known working:

| Alias | Gateway model |
| --- | --- |
| gpt-5 | openai/gpt-5 |
| gemini-pro | google-ai-studio/gemini-2.5-pro |
| gemini-flash | google-ai-studio/gemini-2.5-flash |

Claude and GPT-4o Realtime may require probing because provider-prefixed IDs can produce upstream provider-auth-style errors:

```bash
python run_pipeline.py --probe-model-alias claude-sonnet
python run_pipeline.py --probe-model-alias claude-haiku
python run_pipeline.py --probe-model-alias gpt-4o-realtime
```

You can probe every alias at once:

```bash
python run_pipeline.py --probe-model-alias all
```

## Smoke Tests

Run one model:

```bash
python run_pipeline.py --smoke-test --model gpt-5
python run_pipeline.py --smoke-test --model gemini-pro
python run_pipeline.py --smoke-test --model gemini-flash
python run_pipeline.py --smoke-test --model claude-sonnet
python run_pipeline.py --smoke-test --model claude-haiku
python run_pipeline.py --smoke-test --model gpt-4o-realtime
```

Run all configured aliases:

```bash
python run_pipeline.py --smoke-test-all
```

## Generation Runs

Run GPT-5 with the larger visible-output budget:

```bash
python run_pipeline.py --model gpt-5 --input prompts.jsonl --save-code --output-dir outputs/gpt-5/
```

```bash
python run_pipeline.py \
  --model gpt-5 \
  --input data/prompts.jsonl \
  --save-code \
  --save-raw-output \
  --output-dir outputs/ \
  --max-completion-tokens 8192
```

Run every configured model:

```bash
python run_pipeline.py \
  --model all \
  --input data/prompts.jsonl \
  --save-code \
  --save-raw-output \
  --output-dir outputs/
```

`--save-code` stores the Python artifact used for evaluation. `--save-raw-output` stores the unmodified model response in `outputs/raw/` before the Python-only validator removes markdown fences or rejects non-code wrappers.

## Metrics

- DHR: Dependency Hallucination Rate, detects fake/nonexistent packages.
- ASVR: API Symbol Validity Rate, detects fake functions/classes/methods/attributes.
- CFVR: CLI Command/Flag Validity Rate, detects invalid CLI tools or flags.
- EIPR: Executable Integrity Pass Rate, checks whether code imports and runs.
- RACS: Requirement-Artifact Consistency Score, checks whether output follows prompt requirements.
- RHSR: Recurrent Hallucination Stability Rate, detects repeated hallucination patterns.

We combine existence checks + runtime validation + prompt compliance + recurrence tracking to evaluate hallucinations in generated code.

## Output

Pipeline runs write:

- `trace.jsonl`: structured JSONL events for prompts, generation, normalization, and evaluation.
- `report.jsonl`: aggregate run summary and metric summaries.
- `bundles/`: per-sample JSONL records with generated code and failure reports.
- `<prompt_id>_<model_alias>.py`: cleaned Python code used for evaluation when `--save-code` is enabled.
- `raw/<prompt_id>_<model_alias>.txt`: raw gateway output when `--save-raw-output` is enabled.

Artifact filenames use prompt IDs and local aliases, not gateway model strings with slashes.

## Troubleshooting

### Invalid Anthropic API Key

Do not immediately assume the shared JHU Gateway key is wrong if GPT-5 or Gemini pass. This can mean the Claude model ID or routing format is wrong. Run:

```bash
python run_pipeline.py --probe-model-alias claude-sonnet
python run_pipeline.py --probe-model-alias claude-haiku
```

### You did not provide an API key for GPT-4o Realtime

If other models work, this likely means the endpoint or routing string is wrong for GPT-4o Realtime. Run:

```bash
python run_pipeline.py --probe-model-alias gpt-4o-realtime
```

### GPT-5 empty output

GPT-5 can spend completion tokens on reasoning before producing visible code. The client defaults GPT-5 generation to `reasoning_effort=low`, omits `temperature`, and uses a larger token budget. You can still set the budget explicitly:

```bash
python run_pipeline.py \
  --model gpt-5 \
  --input data/prompts.jsonl \
  --save-code \
  --output-dir outputs/ \
  --max-completion-tokens 8192
```

### Markdown or explanations in generated code

The generator instructs models to output only Python code. The validator strips plain markdown fences, rejects obvious explanation wrappers, and does not rewrite code logic.
# hallucination
