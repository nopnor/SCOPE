---
name: scope-repairer
description: Choose the next repair action inside the SCOPE main workflow. Use after verification when there are failed or uncertain review items, `verification.json.new_unknowns` is empty, and Codex should prepare the repair decision before the parent workflow returns to generate/verify or finalize.
---

# SCOPE Repairer

Use this only as a branch inside `scope-agentic-generation`.

This skill does one job: choose the next repair action once the failure is already understood. It does not discover new unknowns, and it does not run generate, verify, finalize, or eval itself.

Once the parent workflow chooses this branch, this skill owns the full repair branch. The parent must not pre-write `repair.input.json` on this skill's behalf.

Before writing `repair.input.json`, read:

- `references/quality-bar.md`

## When To Run

Run this skill only when all are true:

- `outputs/<run_id>/state.json` exists
- `state.review_results` exists
- `verification.json.new_unknowns` is empty
- at least one review verdict is `fail` or `uncertain`

Do not run this skill when `verify` has already revealed new unknowns. In that case, the parent workflow should route back to retrieval and reasoning first.

## What To Read

Read:

- `outputs/<run_id>/state.json`
- `outputs/<run_id>/verification.json`
- `outputs/<run_id>/synthesis.json` if you need the current prompt wording

Focus on:

- which review items failed
- which `failure_family` is attached to each failed or uncertain review item
- which repair family best matches the dominant failure when `failure_family` is missing
- whether the next step should be `rewrite_prompt`, `image_edit`, `regenerate`, or `none`
- whether the current `final_prompt` should be replaced

## What To Write

Write:

```text
outputs/<run_id>/repair.input.json
```

Shape:

```json
{
  "selected_review_ids": ["math_result"],
  "repair_action": "rewrite_prompt",
  "updated_final_prompt": "A clean poster that explicitly shows 17 + 28 = 45.",
  "repair_patch": {
    "skill": "text_repair",
    "targets": ["math_result"],
    "recommended_action": "rewrite_prompt",
    "diagnosis": "The visible text requirement is failing because the resolved arithmetic answer is not explicit.",
    "additions": ["Make the result 45 explicit."]
  }
}
```

For non-`none` actions, keep the repair focused: smallest useful failure set, valid repair family, non-empty diagnosis, and patch targets aligned with the selected review ids.

Prefer the repair family supplied by verification:

- If failed or uncertain review items include `failure_family`, set `repair_patch.skill` from the dominant `failure_family`.
- If multiple failed items have different families, prioritize in this order: `subject_repair`, `text_repair`, `count_repair`, `relation_repair`, `layout_repair`, `attribute_repair`, `style_repair`.
- If a constraint review has `blocked_by`, prefer repairing the upstream object review named by `blocked_by` before repairing the blocked constraint.
- If `failure_family` is missing, infer the closest allowed family from `item_kind`, `target_id`, `owner_id`, the constraint type in `state.constraints`, and the review reason.

Allowed `repair_patch.skill` values:

- `subject_repair`
- `text_repair`
- `relation_repair`
- `count_repair`
- `attribute_repair`
- `layout_repair`
- `style_repair`

## How To Run

After writing `repair.input.json`, run:

```bash
python -m scope.cli.stage --stage repair --repair-json outputs/<run_id>/repair.input.json --state outputs/<run_id>/state.json
```

This skill owns that full sequence:

1. write `repair.input.json`
2. run the `repair` stage

## What Success Looks Like

Check:

- `repair_decision.json` exists
- `repair_decision.json.source = "agent_json"`
- `state.repair_action` is one of `none`, `rewrite_prompt`, `image_edit`, `regenerate`
- `state.stage_trace` records the action or `repair:skipped:review_passed`

The parent workflow may continue only after all of those checks pass. If any item is missing, the repair branch is incomplete.

## Handoff Back To Parent

After repair succeeds, return control to the parent workflow.

The parent workflow should:

- continue to `finalize` if `state.repair_action = "none"`
- otherwise rerun `generate`, then `verify`

For the wider route and stage contract, see:

- `../scope-agentic-generation/references/main-flow.md`
- `../scope-agentic-generation/references/stage-contracts.md`
