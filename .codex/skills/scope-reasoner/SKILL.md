---
name: scope-reasoner
description: Resolve semantic, arithmetic, or logical unknowns inside the SCOPE main workflow. Use when persisted SCOPE state shows open `semantic_reasoning` gaps and Codex should prepare reasoning resolutions before the parent workflow returns to synthesis.
---

# SCOPE Reasoner

Use this only as a branch inside `scope-agentic-generation`.

This skill does one job: turn open `semantic_reasoning` unknowns into reasoning resolutions. It does not synthesize, generate, verify, repair, finalize, or evaluate.

Once the parent workflow chooses this branch, this skill owns the full reason branch. The parent must not pre-write `reasoning.input.json` on this skill's behalf.

Before writing `reasoning.input.json`, read:

- `references/quality-bar.md`

## When To Run

Run this skill when all are true:

- `outputs/<run_id>/state.json` exists
- `state.unknowns` contains at least one item with `kind = "semantic_reasoning"` and `status = "open"`

Skip this skill when no such unknown exists.

## What To Read

Read:

- `outputs/<run_id>/state.json`
- `outputs/<run_id>/verification.json` if reasoning was triggered by `verify`

Focus on:

- the active `semantic_reasoning` unknowns
- any failed or uncertain review items that reveal a missing inference
- any decomposition constraint that still needs a concrete answer

## What To Write

Write:

```text
outputs/<run_id>/reasoning.input.json
```

Shape:

```json
{
  "reasoning_resolutions": [
    {
      "unknown_id": "u1",
      "note": "The resolved final answer is 45."
    }
  ]
}
```

Each resolution should bind one open unknown to one resolved inference. The `note` is not a process comment; it is the closed semantic result that synthesis can fuse into the next prompt.

## Reasoning Rules

Keep the schema light, but make every `note` a closed answer:

- arithmetic: write the final value, not "calculate it"
- visible text: write the exact text that should appear
- selection: write the selected object, name, value, or option
- multiple-choice worksheet answers: separate the selected option letter from the resolved value. Prefer notes like `selected option: D; resolved value: 140 degrees; visible answer mark should be option D only unless the prompt explicitly requests the numeric value.`
- count: reason only when the count is hidden, computed, or selected; explicit counts go straight to synthesis
- relation: reason only when the relation must be inferred or disambiguated; explicit relations go straight to synthesis
- layout interpretation: reason only when the viewpoint, arrow, map cue, or placement has to be interpreted
- temporal/order reasoning: write the resolved order or final event/state
- evidence-based reasoning: use available retrieval evidence only to close the unknown; do not summarize evidence for its own sake
- insufficient information: say what remains unresolved briefly, and let the parent workflow route back to retrieve if needed

Do not produce a reasoning resolution when the constraint is already explicit enough for synthesis, or when retrieved evidence can be copied directly into synthesis without extra inference.

Default to doing less. If a constraint can be copied into the next synthesis prompt without computation, selection, disambiguation, or evidence interpretation, skip reasoning for it.

## How To Run

After writing `reasoning.input.json`, run:

```bash
python -m scope.cli.stage --stage reason --reasoning-json outputs/<run_id>/reasoning.input.json --state outputs/<run_id>/state.json
```

This skill owns that full sequence:

1. write `reasoning.input.json`
2. run the `reason` stage

## What Success Looks Like

Check:

- `reasoning.json` exists
- `reasoning.json.source = "agent_json"`
- `state.reasoning_resolutions` exists
- the resolved unknowns are no longer left in `state.unknowns` as `open`
- every `reasoning_resolutions[*].note` contains the final result or explicit resolved semantic instruction
- `state.stage_trace` includes `reason`

The parent workflow may continue only after all of those checks pass. If any item is missing, the reason branch is incomplete.

If the parent called the reason stage with no matching unknowns, the expected trace is:

```text
reason:skipped:no_semantic_reasoning_unknown
```

## Handoff Back To Parent

After reasoning succeeds, return control to the parent workflow.

If reasoning was triggered because `verify` discovered new unknowns, the parent should keep the normal loop moving with `synthesize -> generate -> verify`.

The parent workflow should:

- run `scope-retriever` too if external-reference unknowns also exist
- then run `synthesize`

For the wider route and stage contract, see:

- `../scope-agentic-generation/references/main-flow.md`
- `../scope-agentic-generation/references/stage-contracts.md`
