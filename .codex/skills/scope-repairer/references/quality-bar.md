# Repairer Quality Bar

Use this file when preparing `repair.input.json`.

The goal is to choose the smallest useful repair move once the failure is already understood.

## What Good Repair Looks Like

- focus on the smallest failure set worth repairing now
- preserve the semantic meaning of the original task
- choose the least invasive repair action that is still likely to help
- make the patch concrete enough that the next `generate` pass is better targeted

## Action Heuristics

Prefer:

- `rewrite_prompt` for the first light correction when the issue is prompt clarity or emphasis
- `image_edit` for one focused local defect when preserving the rest of the image is valuable
- `regenerate` for structural failure, repeated failed local repair, or non-local recomposition problems
- `none` only when there is no meaningful repair move left and the workflow should stop

## Patch Quality Rules

For non-`none` repair actions:

- `selected_review_ids` should identify the focused failing subset
- `repair_patch.skill` must be one of the allowed repair families
- `repair_patch.targets` should stay inside that subset
- `repair_patch.diagnosis` should name the dominant failure simply and concretely
- `repair_patch.additions`, `clarifications`, and `removals` should be short execution-facing deltas

## Repair Families

Use the narrowest matching family:

- `subject_repair` for object identity, missing subject, wrong subject, or action/role failures
- `text_repair` for exact visible text, numerals, readability, or OCR-like failures
- `count_repair` for required object counts, too many, too few, or duplicate-instance failures
- `relation_repair` for spatial or semantic relations between entities
- `attribute_repair` for color, material, shape, size, logo, or object-property failures
- `layout_repair` for cropping, framing, centering, composition, visibility, or global arrangement failures
- `style_repair` for style, lighting, medium, aesthetic, or rendering-mode failures

Good repair shape:

- diagnose one dominant issue
- clarify the exact missing correction
- preserve already-correct structure when possible

Weak repair shape:

- broad global rewrite for one local defect
- vague patch text like `improve the image`
- changing subject, count, relation, or exact required text into an easier surrogate

## Preserve Semantics

Do not:

- replace a hard requirement with an easier scene
- change who the subject is
- change the required count
- change the intended relation
- change exact visible text into paraphrase
- invent stricter geometric rules that were never in the task

## Done Criteria

The repair output is good enough when:

- the chosen action matches the failure pattern
- the focused targets are explicit
- the diagnosis is non-empty
- the patch gives the next generation step a concrete correction without changing task meaning
