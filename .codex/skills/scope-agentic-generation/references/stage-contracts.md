# SCOPE Stage Contracts

This file is the canonical stage contract for the normal Codex-driven SCOPE path.

Read this file together with `main-flow.md`.

## Contract Summary

In the normal Codex path:

- `decompose`, `retrieve`, `reason`, `synthesize`, and `repair` are agent-first stages
- `generate`, `verify`, `finalize`, and `eval` are tool-first stages

Ownership inside the agent-first stages is split:

- the parent workflow owns `decompose` and `synthesize`
- the branch skills own `retrieve`, `reason`, and `repair`

Agent-first means:

1. Codex writes a stage input artifact.
2. The stage tool validates it.
3. The stage tool persists the canonical artifact.

Tool-first means:

1. Codex triggers the stage command.
2. Python performs the deterministic operation.
3. The stage tool writes the canonical artifact.

## Decompose

Purpose:

- turn the user prompt into `entities`, `constraints`, and `unknowns`

Codex-owned input artifact:

- `outputs/<run_id>/decomposition.input.json`

Required command:

```bash
python -m scope.cli.stage --stage decompose --prompt "<prompt>" --decomposition-json outputs/<run_id>/decomposition.input.json --state outputs/<run_id>/state.json --output-dir outputs/<run_id>
```

Canonical outputs:

- `state.json`
- `decomposition.json`

Completion gate:

- `decomposition.json.source = "agent_json"`
- `state.stage_trace` includes `decompose`
- `state.entities`, `state.constraints`, `state.unknowns` all exist

## Retrieve

Purpose:

- resolve open `external_reference` unknowns into retrieval resolutions

Trigger:

- `state.unknowns` contains at least one item with `kind = "external_reference"` and `status = "open"`

Codex-owned input artifact:

- `outputs/<run_id>/retrieval.input.json`

Search evidence artifact:

- `outputs/<run_id>/search_results.json`, produced by `python -m scope.cli.search --state outputs/<run_id>/state.json --query-plan-json outputs/<run_id>/retrieval.query_plan.json --output-json outputs/<run_id>/search_results.json`
- `outputs/<run_id>/retrieval.query_plan.json`, produced by `scope-retriever`

Ownership rule:

- `scope-retriever` must write `retrieval.query_plan.json`
- `scope-retriever` must run search and write `search_results.json`
- `scope-retriever` must write `retrieval.input.json`
- the parent workflow must not pre-write those files on the branch skill's behalf

Required command:

```bash
python -m scope.cli.stage --stage retrieve --retrieval-json outputs/<run_id>/retrieval.input.json --state outputs/<run_id>/state.json
```

Canonical outputs:

- `retrieval.json`
- updated `state.json`

Completion gate:

- `retrieval.query_plan.json` exists when retrieve is triggered
- `search_results.json` exists when retrieve is triggered
- `search_results.json.query_plan_path` points to `retrieval.query_plan.json` when retrieve is triggered
- if triggered, `retrieval.json.source = "agent_json"`
- if triggered, `state.retrieval_resolutions` exists
- if triggered, the matched `external_reference` unknowns are no longer `open`
- otherwise, trace records `retrieve:skipped:no_external_reference_unknown`

## Reason

Purpose:

- resolve open `semantic_reasoning` unknowns into closed semantic result notes

Trigger:

- `state.unknowns` contains at least one item with `kind = "semantic_reasoning"` and `status = "open"`

Codex-owned input artifact:

- `outputs/<run_id>/reasoning.input.json`

Ownership rule:

- `scope-reasoner` must write `reasoning.input.json`
- `scope-reasoner` must run the `reason` stage command
- the parent workflow must not pre-write `reasoning.input.json` on the branch skill's behalf

Required command:

```bash
python -m scope.cli.stage --stage reason --reasoning-json outputs/<run_id>/reasoning.input.json --state outputs/<run_id>/state.json
```

Canonical outputs:

- `reasoning.json`
- updated `state.json`

Completion gate:

- if triggered, `reasoning.json.source = "agent_json"`
- if triggered, `state.reasoning_resolutions` exists
- if triggered, the matched `semantic_reasoning` unknowns are no longer `open`
- if triggered, each reasoning resolution note contains the final answer or explicit resolved semantic instruction
- otherwise, trace records `reason:skipped:no_semantic_reasoning_unknown`

## Synthesize

Purpose:

- merge prompt plus retrieval notes and closed reasoning results into the next generation prompt
- replace unresolved semantic phrases with resolved results instead of appending the reasoning input

Codex-owned input artifact:

- `outputs/<run_id>/synthesis.input.json`

Required command:

```bash
python -m scope.cli.stage --stage synthesize --synthesis-json outputs/<run_id>/synthesis.input.json --state outputs/<run_id>/state.json
```

Canonical outputs:

- `synthesis.json`
- updated `state.json`

Completion gate:

- `synthesis.json.source = "agent_json"`
- `state.final_prompt` is non-empty
- resolved reasoning values are fused into plain generation-facing wording
- for I2I worksheet or multiple-choice input images, synthesis uses a minimal edit instruction and marks the selected option rather than rewriting the whole worksheet or expanding to a full numeric answer
- source expressions, formulas, clues, or reasoning inputs are omitted unless the user explicitly requested them to be visible
- any resolved unknowns consumed by this synthesis pass are no longer marked `resolved`
- `state.stage_trace` includes `synthesize`

## Generate

Purpose:

- create or edit the next image attempt using the current prompt and state

Required command:

```bash
python -m scope.cli.stage --stage generate --state outputs/<run_id>/state.json --output-dir outputs/<run_id>
```

Canonical outputs:

- `generation.json`
- `iteration_XX.image.*`
- updated `state.json`

Completion gate:

- `state.iteration` increased
- `state.last_image_path` points to an existing file
- `state.stage_trace` includes `generate`

## Verify

Purpose:

- judge the latest image against the decomposed entities, constraints, current prompt, checklist, and generated artifact using Codex-authored review results on the normal path
- verify objects first, then object-bound constraints, so downstream constraints are not marked pass when their target object is absent or visually wrong
- discover new unknowns when the failure is actually an information gap

Codex-owned input artifact:

- `outputs/<run_id>/verification.input.json`

Required command:

```bash
python -m scope.cli.stage --stage verify --verification-json outputs/<run_id>/verification.input.json --state outputs/<run_id>/state.json
```

Canonical outputs:

- `verification.json`
- updated `state.json`

Completion gate:

- `verification.json.source = "agent_json"` on the normal Codex-owned path
- `verification.json.entities`, `verification.json.constraints`, and `verification.json.unknowns_before_verify` record the structured state inspected for verification
- `review_results` include object/entity reviews before dependent constraint reviews
- failed or uncertain review items include `failure_family` when the issue is an understood repair problem
- dependent constraints blocked by object failures include `blocked_by`
- I2I worksheet or multiple-choice outputs are checked for minimal edit behavior and visible selected-option marking
- `state.review_results` exists
- `state.verification_unknowns` exists, even if empty
- if `verification.json.new_unknowns` is non-empty, those unknowns are merged into `state.unknowns` as open owner-aligned gaps
- `state.stage_trace` includes `verify`

Routing rule:

- if `verification.json.new_unknowns` is non-empty, go back to conditional augmentation and continue the main loop with `synthesize -> generate -> verify`
- otherwise, if any review verdict is `fail` or `uncertain`, run `repair`
- otherwise, continue to `finalize`

## Repair

Purpose:

- choose the next repair action once the problem is already understood

Trigger:

- `verification.json.new_unknowns` is empty
- at least one `review_results[*].verdict` is `fail` or `uncertain`

Codex-owned input artifact:

- `outputs/<run_id>/repair.input.json`

Ownership rule:

- `scope-repairer` must write `repair.input.json`
- `scope-repairer` must run the `repair` stage command
- the parent workflow must not pre-write `repair.input.json` on the branch skill's behalf

Required command:

```bash
python -m scope.cli.stage --stage repair --repair-json outputs/<run_id>/repair.input.json --state outputs/<run_id>/state.json
```

Canonical outputs:

- `repair_decision.json`
- `repair_history.json`
- updated `state.json`

Completion gate:

- `state.repair_action` is one of `none`, `rewrite_prompt`, `image_edit`, `regenerate`
- `repair_decision.json.source = "agent_json"` in the normal path
- trace records the chosen repair action or `repair:skipped:review_passed`

Routing rule:

- if `repair_action != none`, rerun `generate -> verify`
- if `repair_action = none`, continue to `finalize`

## Finalize

Purpose:

- persist the final prompt used for the run summary

Required command:

```bash
python -m scope.cli.stage --stage finalize --state outputs/<run_id>/state.json --output-dir outputs/<run_id>
```

Canonical outputs:

- `final_prompt.txt`
- `finalization.json`

Completion gate:

- `final_prompt.txt` exists
- `state.stage_trace` includes `finalize`

## Eval

Purpose:

- compute run-level metrics from artifacts

Required command:

```bash
python -m scope.cli.evaluate --run-dir outputs/<run_id>
```

Canonical outputs:

- `metrics.json`

Completion gate:

- `metrics.json` exists
- metrics are derived from artifacts, not chat content
