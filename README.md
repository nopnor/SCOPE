# SCOPE

SCOPE is an agent-facing workflow for complex image generation. It keeps user intent as persistent entity and constraint commitments, then uses retrieval, reasoning, verification, and repair steps to improve faithful generation.

The repository contains:

- Codex workflow skills under `.codex/skills`
- Python stage tools under `src/scope`
- runtime configuration examples under `configs`
- Gen-Arena materialization and evaluation utilities for the public [Gen-Arena dataset](https://huggingface.co/datasets/rentianfei122/Gen-Arena)

For the design rationale, see [docs/architecture.md](docs/architecture.md).

## Installation

Requirements:

- Python 3.10+
- Codex CLI for full agent-driven workflow execution
- Optional API keys for image generation, verification, and web search backends

Install the package in editable mode:

```bash
pip install -e .
```

For local tests:

```bash
pip install -e ".[dev]"
pytest -q
```

## Configuration

The runtime reads environment variables directly. Load the example settings in your shell, or copy the file into whatever environment manager you use:

```bash
set -a
source configs/scope.example.env
set +a
```

Main settings:

- `SCOPE_IMAGE_PROVIDER`: `jdcloud_gemini`, `gemini_jdcloud`, or `jdcloud`
- `SCOPE_IMAGE_API_KEY`: image generation API key
- `SCOPE_JUDGE_PROVIDER`: `jdcloud_gemini`, `gemini_jdcloud`, or `jdcloud`
- `SCOPE_JUDGE_API_KEY`: optional verifier API key
- `SCOPE_SEARCH_PROVIDER`: `serper`
- `SCOPE_SERPER_API_KEY`: optional Serper API key for external-reference retrieval

The corresponding YAML example is [configs/scope.example.yaml](configs/scope.example.yaml).

## Quick Start

The normal SCOPE path is Codex-driven. The main workflow skill is:

```text
.codex/skills/scope-agentic-generation/SKILL.md
```

In a Codex session, run a case by asking Codex to use that skill with your prompt and an output directory. The Python commands are deterministic stage tools; Codex owns the decomposition, retrieval, reasoning, synthesis, verification, and repair payloads.

The workflow stages are:

```text
decompose -> retrieve/reason when needed -> synthesize -> generate -> verify -> repair when needed -> finalize -> evaluate
```

Each agent-authored stage writes an input artifact first. The stage command validates it and persists the canonical artifact.

Required or conditional input artifacts:

- `decomposition.input.json`
- `retrieval.input.json` when retrieval is needed
- `reasoning.input.json` when reasoning is needed
- `synthesis.input.json`
- `verification.input.json`
- `repair.input.json` when repair is needed

Minimal stage validation example:

```bash
mkdir -p outputs/demo

cat > outputs/demo/decomposition.input.json <<'JSON'
{
  "entities": [
    {"id": "o1", "name": "math poster", "priority": "primary"}
  ],
  "constraints": [
    {
      "id": "c1",
      "text": "The poster shows the correct result 45 in large readable numerals.",
      "type": "text",
      "priority": "critical",
      "spec": {"require_readable_text": true}
    }
  ],
  "unknowns": []
}
JSON

python -m scope.cli.stage \
  --stage decompose \
  --prompt "A clean math poster showing the correct result of 17 + 28 in large readable numerals." \
  --decomposition-json outputs/demo/decomposition.input.json \
  --state outputs/demo/state.json \
  --output-dir outputs/demo
```

This validates and persists the first-stage artifact. A complete generation run should continue through the Codex-owned workflow skill. After a complete run, compute workflow metrics:

```bash
python -m scope.cli.evaluate --run-dir outputs/demo
```

Typical outputs include:

- `state.json`
- `final_prompt.txt`
- generated image files
- `metrics.json`
- stage artifacts such as `decomposition.json`, `synthesis.json`, and `verification.json`

## Gen-Arena

This repository does not bundle benchmark data. Download the public [Gen-Arena dataset](https://huggingface.co/datasets/rentianfei122/Gen-Arena) from Hugging Face and point the tools to your local copy.

Materialize Gen-Arena `eval.jsonl` files into separate runtime and evaluation packets:

```bash
scope-benchmark materialize-gen-arena-eval \
  --dataset-root /path/to/Gen-Arena \
  --output-root benchmark/materialized/gen_arena
```

This creates:

```text
benchmark/materialized/gen_arena/<case_id>/benchmark_input.json
benchmark/materialized/gen_arena/<case_id>/benchmark_eval.json
benchmark/materialized/gen_arena/cases.index.json
```

Dispatch materialized cases to independent Codex runs:

```bash
scope-benchmark run-codex \
  --index benchmark/materialized/gen_arena/cases.index.json \
  --run-root outputs/gen-arena-full \
  --concurrency 10
```

Summarize workflow metrics:

```bash
scope-benchmark summarize --run-root outputs/gen-arena-full
```

Evaluate Gen-Arena entity and constraint commitments:

```bash
scope-gen-arena-eval \
  --materialized-root benchmark/materialized/gen_arena \
  --run-root outputs/gen-arena-full
```

For prompt-only generation cases, use a JSONL file with `id` and `prompt`:

```bash
scope-benchmark materialize-gen-arena \
  --prompt-jsonl /path/to/Gen-Arena/category/prompt.jsonl \
  --output-root benchmark/materialized/gen_arena_prompt_only
```

## Command Reference

- `scope-stage`: validate and persist one workflow stage
- `scope-eval`: summarize one completed run directory
- `scope-search`: collect web/image evidence for retrieval branches
- `scope-benchmark`: materialize and dispatch benchmark cases
- `scope-gen-arena-eval`: compute Gen-Arena entity/constraint metrics

The same commands can also be run as modules, for example:

```bash
python -m scope.cli.stage --help
python -m scope.cli.benchmark --help
python -m scope.cli.gen_arena_eval --help
```

## Repository Layout

```text
.codex/skills/      Codex workflow skills
configs/            Runtime configuration examples
docs/               Architecture notes
src/scope/          Python stage tools, contracts, and backends
tests/              Unit and smoke tests
```

## License

This project is released under the MIT License. See [LICENSE](LICENSE).
