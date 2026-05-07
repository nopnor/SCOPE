# SCOPE Main Flow

This file is the canonical routing rule for the normal Codex-driven SCOPE path.

Read this file before running the workflow. Then read `stage-contracts.md` for the stage-level input/output contract.

## Core Rule

SCOPE has one fixed main workflow. Only conditional branches are delegated to child skills.

```text
prompt
-> decompose
-> conditional augmentation
-> synthesize
-> generate
-> verify
-> conditional augmentation or repair
-> generate
-> verify
-> ...
-> finalize
-> eval
```

The fixed main line is:

1. `decompose`
2. conditional augmentation:
   - `retrieve` when `state.unknowns` contains at least one open `external_reference`
   - `reason` when `state.unknowns` contains at least one open `semantic_reasoning`
3. `synthesize`
4. `generate`
5. `verify`
6. routing after `verify`:
   - if `verification.json.new_unknowns` is non-empty, route back through conditional augmentation, then run `synthesize`, then continue with `generate`
   - otherwise, if any review verdict is `fail` or `uncertain`, run `repair`, then continue with `generate`
   - otherwise, run `finalize`
7. `eval`

## Branch Ownership

The parent workflow owns the fixed main line and its top-level artifacts:

- `decomposition.input.json`
- `synthesis.input.json`
- `verification.input.json`
- the routing decision after each persisted stage result

The branch skills own their own branch artifacts and stage calls:

- `scope-retriever`: `retrieval.query_plan.json`, `search_results.json`, `retrieval.input.json`, then `retrieve`
- `scope-reasoner`: `reasoning.input.json`, then `reason`
- `scope-repairer`: `repair.input.json`, then `repair`

The parent must not handwrite branch input artifacts in advance. Once a branch is triggered, that branch skill becomes responsible for writing its own inputs and running its own stage command.

## Skill Map

The fixed main skill is:

- `scope-agentic-generation`

The conditional workflow branch skills are:

- `scope-retriever`
- `scope-reasoner`
- `scope-repairer`

## Routing Rules

### Initial augmentation

After `decompose`, inspect persisted `state.unknowns`.

- If any unknown has `kind = "external_reference"` and `status = "open"`, run `scope-retriever`.
- If any unknown has `kind = "semantic_reasoning"` and `status = "open"`, run `scope-reasoner`.
- Both may run in the same cycle.
- If neither applies, do not invent augmentation work.
- Reasoning outputs are closed semantic results in `state.reasoning_resolutions[*].note`. Downstream synthesis should fuse those results with the original prompt and constraints by replacing unresolved semantic phrases with the resolved result. Do not keep the source expression, clue, formula, or reasoning input unless the user explicitly asked to show it.
- For I2I worksheet or multiple-choice input images, downstream synthesis should perform the smallest edit needed to mark the selected option and preserve the original image rather than regenerating the full worksheet.

Then run `synthesize`.

Before the parent continues past conditional augmentation, every triggered branch must pass its branch completion gate:

- retrieve gate:
  - `retrieval.query_plan.json` exists
  - `search_results.json` exists
  - `retrieval.json.source = "agent_json"`
  - the matched `external_reference` unknowns are no longer `open`
- reason gate:
  - `reasoning.json` exists
  - `reasoning.json.source = "agent_json"` when reasoning was triggered
  - the matched `semantic_reasoning` unknowns are no longer `open`

If a triggered branch has not passed its gate, do not continue to `synthesize`.

### Verification-driven augmentation

After every `verify`:

- Read `verification.json`.
- On the normal Codex-owned path, `verification.json.source` must be `agent_json`.
- Verification must be grounded in the decomposed `entities`, `constraints`, and current unknown status, not only the synthesized prompt string.
- Verification is object-first: check entity presence/identity/reference match before judging constraints attached to that entity.
- Failed or uncertain review items should carry `failure_family` so the repair branch can route to the right repair skill.
- If `new_unknowns` is non-empty, treat that as a newly discovered information gap.
- A newly discovered unknown means the main loop should continue, not stop.
- Do not run `repair` first in that case.
- Instead, let the fixed main flow go back through conditional augmentation:
  - `retrieve` for new `external_reference` unknowns
  - `reason` for new `semantic_reasoning` unknowns
- Then rerun `synthesize`.
- Then rerun `generate`.
- Then rerun `verify`.

This keeps `verify` responsible for discovering new unknowns, while `repair` stays responsible for repair planning over already-understood failures.

### Repair routing

Run `repair` only when both are true:

- `verification.json.new_unknowns` is empty
- at least one `review_results[*].verdict` is `fail` or `uncertain`

`repair` decides one of:

- `rewrite_prompt`
- `image_edit`
- `regenerate`
- `none`

If `repair_action = none`, the loop can stop and continue to `finalize`.

If `repair_action != none`, rerun:

```text
generate -> verify
```

Before the parent continues past repair, the repair branch must pass its completion gate:

- `repair_decision.json` exists
- `repair_decision.json.source = "agent_json"`
- `state.repair_action` is valid
- `state.stage_trace` records the repair action or `repair:skipped:review_passed`

If the repair branch has not passed its gate, do not continue to `generate` or `finalize`.

### Stop condition

The workflow stops when one of these is true:

- every review verdict is `pass`
- `max_iterations` generation attempts have been used

If the iteration limit is hit, still run `finalize` and `eval`. Report the run as incomplete or failed according to `metrics.json`.

## Artifact Discipline

The workflow is artifact-driven.

- Do not edit `state.json` manually to push the workflow forward.
- Do not infer progress from chat history.
- Use stage tools to validate and persist each transition.
- Use persisted artifacts to decide the next step.

The canonical routing artifacts are:

- `state.json`
- `decomposition.json`
- `search_results.json`
- `retrieval.json`
- `reasoning.json`
- `synthesis.json`
- `verification.json`
- `repair_decision.json`
- `final_prompt.txt`
- `metrics.json`

## Ownership Split

Codex owns:

- prompt understanding
- decomposition
- synthesis content
- verification review content
- branch decisions based on persisted artifacts
- the child branch skills that produce retrieval, reasoning, and repair content

Python owns:

- validating stage payloads
- persisting canonical artifacts
- image backend calls
- optional judge backend fallback when `verification.input.json` is not provided
- evaluation from artifacts

Python does not own the top-level orchestration rule in the normal Codex path.
