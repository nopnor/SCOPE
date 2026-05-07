---
name: scope-agentic-generation
description: Run the full SCOPE image-generation workflow for one prompt. Use when Codex should own the fixed main flow, write the top-level stage input artifacts, route retrieve/reason/repair into child branch skills from persisted state, and finish with finalize plus eval.
---

# SCOPE Agentic Generation

Use this as the top-level SCOPE skill for a single prompt.

This skill is the workflow director. It decides the route from artifacts on disk. Python stage commands only validate payloads, persist canonical artifacts, and call deterministic backends.

Do not edit `state.json` by hand to fake progress.

## Branch Ownership Rule

The parent workflow owns:

- `decomposition.input.json`
- `synthesis.input.json`
- `verification.input.json`
- stage routing decisions from persisted artifacts
- `generate`, `verify`, `finalize`, and `eval`

The branch skills own their own branch artifacts and stage calls:

- `scope-retriever` owns `retrieval.query_plan.json`, `search_results.json`, `retrieval.input.json`, and the `retrieve` stage call
- `scope-reasoner` owns `reasoning.input.json` and the `reason` stage call
- `scope-repairer` owns `repair.input.json` and the `repair` stage call

When a branch is triggered, the parent must hand off that branch and wait for the branch completion gate. Do not pre-write branch input files in the parent just to hurry the workflow forward.

The parent must not handwrite:

- `retrieval.query_plan.json`
- `search_results.json`
- `retrieval.input.json`
- `reasoning.input.json`
- `repair.input.json`

## Unknown-First Rule

When `verify` emits `verification.json.new_unknowns`, treat that as a newly discovered information gap inside the fixed main loop.

- Do not stop the run just because a new unknown was found.
- Do not go to `repair` first.
- Merge the new unknowns back into the normal branch triggers.
- Run `retrieve` for open `external_reference` unknowns.
- Run `reason` for open `semantic_reasoning` unknowns.
- Then continue the main line with `synthesize -> generate -> verify`.

`repair` is only for failures that are already understood after `verify` and no longer require new information.

## Inputs

Required:

- `prompt`

Optional:

- `run_id`
- `output_dir`, default `outputs/<run_id>`
- `max_iterations`, default `3`
- `benchmark_input_json`, optional benchmark runtime input packet to bind during `decompose`

Primary state path:

```text
outputs/<run_id>/state.json
```

## Stage Commands

Run these from the repository root:

```bash
python -m scope.cli.stage --stage decompose --prompt "<prompt>" --decomposition-json outputs/<run_id>/decomposition.input.json [--benchmark-input-json benchmark/materialized/<bench>/<case_id>/benchmark_input.json] --state outputs/<run_id>/state.json --output-dir outputs/<run_id>
python -m scope.cli.search --state outputs/<run_id>/state.json --output-json outputs/<run_id>/search_results.json
python -m scope.cli.stage --stage retrieve --retrieval-json outputs/<run_id>/retrieval.input.json --state outputs/<run_id>/state.json
python -m scope.cli.stage --stage reason --reasoning-json outputs/<run_id>/reasoning.input.json --state outputs/<run_id>/state.json
python -m scope.cli.stage --stage synthesize --synthesis-json outputs/<run_id>/synthesis.input.json --state outputs/<run_id>/state.json
python -m scope.cli.stage --stage generate --state outputs/<run_id>/state.json --output-dir outputs/<run_id>
python -m scope.cli.stage --stage verify --verification-json outputs/<run_id>/verification.input.json --state outputs/<run_id>/state.json
python -m scope.cli.stage --stage repair --repair-json outputs/<run_id>/repair.input.json --state outputs/<run_id>/state.json
python -m scope.cli.stage --stage finalize --state outputs/<run_id>/state.json --output-dir outputs/<run_id>
python -m scope.cli.evaluate --run-dir outputs/<run_id>
```

## Fixed Main Flow

Always run this workflow shape:

1. `decompose`
2. conditional augmentation:
   - run `scope-retriever` when `state.unknowns` contains any open `external_reference`
   - run `scope-reasoner` when `state.unknowns` contains any open `semantic_reasoning`
   - after `reason`, treat `state.reasoning_resolutions[*].note` as the resolved semantic result for synthesis
3. `synthesize`
4. `generate`
5. `verify`
6. branch from `verify`
7. `finalize`
8. evaluation

The branch after `verify` is fixed:

- if `verification.json.new_unknowns` is non-empty:
  - treat this as "continue the loop with more information", not as a stop condition
  - go back through conditional augmentation
  - rerun `synthesize`
  - rerun `generate`
  - rerun `verify`
- otherwise, if any `review_results[*].verdict` is `fail` or `uncertain`:
  - run `scope-repairer`
  - if `repair_action != "none"`, rerun `generate`
  - rerun `verify`
- otherwise:
  - continue to `finalize`

Stop when either:

- all review verdicts are `pass`
- `max_iterations` generation attempts have been used

If the iteration limit is hit, still run `finalize` and evaluation.

## Main-Loop Playbook

### 1. Decompose

Write:

```text
outputs/<run_id>/decomposition.input.json
```

Required shape:

```json
{
  "entities": [
    {"id": "o1", "name": "mug", "priority": "primary"}
  ],
  "constraints": [
    {
      "id": "c1",
      "text": "The mug is blue.",
      "type": "attribute",
      "priority": "major",
      "spec": {"target_id": "o1", "attribute": "color", "value": "blue"}
    }
  ],
  "unknowns": [
    {
      "id": "u1",
      "kind": "semantic_reasoning",
      "owner_id": "c1",
      "owner_kind": "constraint",
      "question": "What exact result should be shown?"
    }
  ]
}
```

Then run `decompose`.

Check:

- `decomposition.json` exists
- `decomposition.json.source = "agent_json"`
- `state.stage_trace` includes `decompose`
- `state.entities`, `state.constraints`, and `state.unknowns` all exist

If this run came from a benchmark case, pass the runtime input packet during `decompose`:

```bash
python -m scope.cli.stage --stage decompose --prompt "<prompt>" --decomposition-json outputs/<run_id>/decomposition.input.json --benchmark-input-json benchmark/materialized/<bench>/<case_id>/benchmark_input.json --state outputs/<run_id>/state.json --output-dir outputs/<run_id>
```

This only injects runtime-visible benchmark inputs needed by the main flow. Eval-only fields stay outside the main state in the sibling `benchmark_eval.json`, and `benchmark_binding.json` only records the slim runtime view plus the eval-packet path. It does not change the orchestration rule.

### 2. Conditional augmentation

Read `state.json`.

- If any unknown is `external_reference`, trigger `scope-retriever`.
- If any unknown is `semantic_reasoning`, trigger `scope-reasoner`.
- Only treat unknowns with `status = "open"` as active branch triggers.
- Both may run in the same cycle.
- If neither kind exists, do not invent augmentation work.
- Do not write branch artifacts in the parent before invoking a branch skill.
- After each triggered branch returns, enforce its branch completion gate before continuing.

Retrieve branch completion gate:

- `retrieval.query_plan.json` exists
- `search_results.json` exists and points back to `retrieval.query_plan.json`
- `retrieval.json` exists
- `retrieval.json.source = "agent_json"`
- the matched `external_reference` unknowns are no longer `open`
- `state.stage_trace` includes `retrieve`

Reason branch completion gate:

- `reasoning.json` exists
- `reasoning.json.source = "agent_json"` when reasoning was triggered
- `state.reasoning_resolutions` exists
- the matched `semantic_reasoning` unknowns are no longer `open`
- `state.stage_trace` includes `reason` or `reason:skipped:no_semantic_reasoning_unknown`

If any triggered branch fails its gate, the branch is incomplete. Fix that branch first. Only after all triggered branches pass their gates may the parent continue to `synthesize`.

### 3. Synthesize

When reasoning has run, read `state.reasoning_resolutions[*].note` together with the original prompt and constraints. The note already contains the reasoning result; synthesis should fuse it into `final_prompt` instead of doing another hidden reasoning pass.

Use semantic substitution, not mechanical concatenation:

- If a reasoning note resolves an expression, riddle, implicit value, or hidden target, replace the unresolved phrase with the resolved result.
- Do not preserve the source expression, clue, formula, or reasoning input unless the user explicitly asked to show it.
- If the user asked to show the process, equation, clue, or both input and answer, include both.
- Remove process words such as `calculate`, `infer`, `unknown`, `needs reasoning`, or `reasoning result`.
- When a screen, board, poster, label, or other surface carries required text, make the text fully visible and unobstructed in the final prompt; place people or objects so they do not cover required words.

For I2I worksheet or multiple-choice input images, use minimal image editing:

- Preserve the original input image layout, diagram, choices, and text as much as possible.
- If the prompt asks to write/show the answer in the corresponding position and the input image contains choices/options, mark the selected option visually instead of rewriting the whole question.
- If there is an answer blank or parentheses, fill only the selected option letter (for example `D`), not the full option text or numeric value, unless the user explicitly asks for the value.
- Acceptable visual marks include circling, checking, underlining, or placing the option letter in the blank.
- Do not generate a new worksheet from scratch when an input image is provided; perform the smallest edit needed to show the answer.

Write:

```text
outputs/<run_id>/synthesis.input.json
```

Example:

```json
{
  "final_prompt": "A clean physical poster on a wall with large readable text: \"45\", no extra text, no distracting objects.",
  "synthesis_notes": [
    "Replaced the unresolved arithmetic expression with the resolved final answer."
  ]
}
```

Then run `synthesize`.

Check:

- `synthesis.json` exists
- `synthesis.json.source = "agent_json"`
- `state.final_prompt` is non-empty

### 4. Generate

Run `generate`.

Check:

- `generation.json` exists
- `state.iteration` increased
- `state.last_image_path` points to an existing file

### 5. Verify

Read `state.json`, `generation.json`, and the current generated image at `state.last_image_path`.

Verify against the structured state, not just the final prompt:

- Use `state.entities` as the object set that should be checked for presence, identity, count, and priority.
- Use `state.constraints` as the authoritative requirement list; critical constraints should not be softened just because the synthesized prompt wording is shorter.
- Check `state.unknowns` by status. Open unknowns at verify time indicate an unresolved information gap and should normally produce `uncertain` plus `new_unknowns`. Resolved or consumed unknowns should be checked through `state.retrieval_resolutions`, `state.reasoning_resolutions`, and the actual image result.
- Do not use eval-only benchmark files or sibling `benchmark_eval.json`.
- For I2I worksheet or multiple-choice input images, verify minimal edit behavior: original content should be preserved, the selected option should be visually marked, and the output should not rewrite the whole worksheet or replace the mark with a verbose full answer unless explicitly requested.

Use a two-pass verification order:

1. Object/entity reviews first.
   - Write one review item for each primary entity and for any supporting entity that carries a critical constraint.
   - Check whether the object is present, visually identifiable, counted correctly, and given the right priority.
   - If the entity has an `external_reference` resolution with image evidence (`evidence[*].local_path`) or appears in `state.reference_images`, compare the generated image against the runtime reference image(s). Do not rely on the object name alone.
   - If the object is absent, generic, or inconsistent with its runtime reference image, mark the object review `fail` or `uncertain` and use `failure_family = "subject_repair"`.
2. Constraint reviews second.
   - Review every critical constraint and any major constraint that materially affects the request.
   - Bind constraints to objects through `constraint.spec.target_id`, object ids mentioned in `constraint.spec`, or the nearest entity named by the constraint text.
   - If the target object review failed or is uncertain, do not pass the dependent constraint. Mark it `uncertain`, set `blocked_by` to the object review id, and explain that the constraint is blocked by object identity/presence.
   - If the target object passed, judge the specific constraint type: `text`, `count`, `relation`, `layout`, `attribute`, or `style`.
   - For text-bearing constraints, required text must be legible, complete, and visually unobstructed. If any required word or name is cropped, covered by a person/object, blurred, garbled, or only inferable from context, mark the constraint `fail` or `uncertain` with `failure_family = "text_repair"`.

For every `fail` or `uncertain` review item, include a repair family:

- `subject_repair`: missing/wrong/generic object identity, including reference-image mismatch.
- `text_repair`: wrong, missing, unreadable, garbled, or incorrectly formatted visible text.
- `count_repair`: wrong number of objects or choices.
- `relation_repair`: wrong object relationship or interaction.
- `layout_repair`: wrong placement, view, ordering, scale, framing, or composition.
- `attribute_repair`: wrong color, material, state, weather, option, or non-text attribute.
- `style_repair`: wrong visual style, rendering quality, mood, or medium.

Write:

```text
outputs/<run_id>/verification.input.json
```

Shape:

```json
{
  "review_results": [
    {
      "id": "object_o1",
      "verdict": "pass",
      "reason": "The primary object is present and visually matches the runtime reference.",
      "item_kind": "object",
      "target_id": "o1",
      "owner_id": "o1",
      "failure_family": "",
      "blocked_by": "",
      "confidence": 0.85,
      "evidence": "Concise visible evidence from the generated artifact."
    }
  ],
  "new_unknowns": []
}
```

Use `fail` or `uncertain` when the image artifact does not visibly satisfy an entity or constraint. Only emit `new_unknowns` when the next attempt needs missing external reference knowledge or unresolved semantic reasoning; otherwise leave `new_unknowns` empty and let repair handle understood failures.

Then run `verify`:

```bash
python -m scope.cli.stage --stage verify --verification-json outputs/<run_id>/verification.input.json --state outputs/<run_id>/state.json
```

Check:

- `verification.json` exists
- `verification.json.source = "agent_json"`
- `state.review_results` exists
- `state.verification_unknowns` exists, even if empty

Interpret the result in this order:

1. if `verification.json.new_unknowns` is non-empty, go back to conditional augmentation, then continue `synthesize -> generate -> verify`
2. else if any review verdict is `fail` or `uncertain`, trigger `scope-repairer`
3. else continue to `finalize`

### 6. Repair branch

Run `scope-repairer` only when:

- `verification.json.new_unknowns` is empty
- at least one review verdict is `fail` or `uncertain`

The parent must not write `repair.input.json` itself. Hand the repair branch to `scope-repairer` and wait for the repair completion gate.

Repair branch completion gate:

- `repair_decision.json` exists
- `repair_decision.json.source = "agent_json"`
- `state.repair_action` is one of `none`, `rewrite_prompt`, `image_edit`, `regenerate`
- `state.stage_trace` records the repair action or `repair:skipped:review_passed`

After `scope-repairer`:

- if `state.repair_action = "none"`, continue to `finalize`
- otherwise rerun `generate`, then `verify`

### 7. Finalize

Run `finalize`.

Check:

- `final_prompt.txt` exists
- `finalization.json` exists

### 8. Eval

Run the evaluation command.

Check:

- `metrics.json` exists

## Workflow Branch Skills

Use these only at the branch points above:

- `scope-retriever`
- `scope-reasoner`
- `scope-repairer`

## Failure Handling

- If a required input artifact is missing, regenerate that artifact and rerun the same stage.
- If a stage payload is invalid, rewrite only that payload and rerun the same stage.
- If `verify` reveals `new_unknowns`, do not repair first and do not stop the normal loop. Route back through augmentation, then continue `synthesize -> generate -> verify`.
- Do not skip `verify` before `repair`.
- Do not run `eval` before `finalize`.

For the expanded route spec and the detailed stage contract, use:

- `references/main-flow.md`
- `references/stage-contracts.md`

## Reporting

At the end, report:

- run directory
- final prompt path
- latest image path
- metrics path
- actual `stage_trace`
- triggered child skills
- skipped child skills and skip reasons
- whether the run passed
