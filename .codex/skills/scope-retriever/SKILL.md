---
name: scope-retriever
description: Resolve external-reference unknowns inside the SCOPE main workflow. Use when persisted SCOPE state shows open `external_reference` gaps and Codex should prepare retrieval resolutions before the parent workflow returns to synthesis.
---

# SCOPE Retriever

Use this only as a branch inside `scope-agentic-generation`.

This skill does one job: turn open `external_reference` unknowns into retrieval resolutions. It does not synthesize, generate, verify, repair, finalize, or evaluate.

Once the parent workflow chooses this branch, this skill owns the full retrieve branch. The parent must not pre-write `retrieval.query_plan.json`, `search_results.json`, or `retrieval.input.json` on this skill's behalf.

Before writing `retrieval.input.json`, read:

- `references/quality-bar.md`

## When To Run

Run this skill when all are true:

- `outputs/<run_id>/state.json` exists
- `state.unknowns` contains at least one item with `kind = "external_reference"` and `status = "open"`

Skip this skill when no such unknown exists.

## What To Read

Read:

- `outputs/<run_id>/state.json`
- `outputs/<run_id>/verification.json` if retrieval was triggered by `verify`
- `outputs/<run_id>/search_results.json` after running search

Focus on:

- the active `external_reference` unknowns
- the unknown owner (`owner_kind`, `owner_id`, `owner_name`)
- whether the gap needs text search, image search, or both

## Query Planning

Before search, write:

```text
outputs/<run_id>/retrieval.query_plan.json
```

Shape:

```json
{
  "query_plans": [
    {
      "unknown_id": "u1",
      "text_queries": ["short text query"],
      "image_queries": ["short image query"]
    }
  ]
}
```

Rules:

- bind every plan to one open `external_reference` unknown by `unknown_id`
- keep queries short and search-engine-native
- separate fact/data lookup into `text_queries`
- separate visual appearance lookup into `image_queries`
- do not paste the full original prompt into a query
- do not include generation verbs such as `create`, `generate`, `draw`, or `make`
- do not add generic filler such as `appearance`, `what does ... look like`, `image`, `picture`, or `visual reference`
- do not search generic objects unless the prompt makes them externally specific
- for people, characters, public figures, brands, products, logos, or named IPs, use at most 2 image query variants
- put the narrowest identity query first; use the second only as a fallback or view-specific variant

Good image query examples:

- `MrBeast portrait`
- `Coca-Cola can official`
- `Coca-Cola can front view`
- `Beijing National Stadium Bird's Nest`

Weak image query examples:

- `Create an image of MrBeast standing in a room`
- `What does the Coca-Cola can look like visual reference appearance`
- `official visual reference image of the subject`

## Retrieval Resolution

After search, write:

Write:

```text
outputs/<run_id>/retrieval.input.json
```

Shape:

```json
{
  "retrieval_resolutions": [
    {
      "unknown_id": "u1",
      "note": "Use the official reference image to preserve the requested branded appearance.",
      "evidence": [
        {
          "kind": "web",
          "title": "Official reference page",
          "url": "https://example.com/reference",
          "snippet": "Short source-grounded detail.",
          "query": "subject official"
        }
      ]
    }
  ]
}
```

Each resolution should bind one open unknown to one concrete generation-facing note. The note should answer the current reference gap clearly enough for `synthesize` to use on the next pass.

## How To Run

First run web search for the open external-reference unknowns:

```bash
python -m scope.cli.search --state outputs/<run_id>/state.json --query-plan-json outputs/<run_id>/retrieval.query_plan.json --output-json outputs/<run_id>/search_results.json
```

Use `search_results.json` as evidence when writing `retrieval.input.json`.

Then run:

```bash
python -m scope.cli.stage --stage retrieve --retrieval-json outputs/<run_id>/retrieval.input.json --state outputs/<run_id>/state.json
```

This skill owns that full sequence:

1. write `retrieval.query_plan.json`
2. run search and write `search_results.json`
3. write `retrieval.input.json`
4. run the `retrieve` stage

## What Success Looks Like

Check:

- `retrieval.query_plan.json` exists and uses short owner-aligned queries
- `search_results.json.query_plan_path` points to `retrieval.query_plan.json`
- `retrieval.json` exists
- `retrieval.json.source = "agent_json"`
- `state.retrieval_resolutions` exists
- each triggered resolution includes evidence unless search was unavailable or returned no useful source
- the resolved unknowns are no longer left in `state.unknowns` as `open`
- `state.stage_trace` includes `retrieve`

The parent workflow may continue only after all of those checks pass. If any item is missing, the retrieve branch is incomplete.

If the parent called the retrieve stage with no matching unknowns, the expected trace is:

```text
retrieve:skipped:no_external_reference_unknown
```

## Handoff Back To Parent

After retrieval succeeds, return control to the parent workflow.

If retrieval was triggered because `verify` discovered new unknowns, the parent should keep the normal loop moving with `synthesize -> generate -> verify`.

The parent workflow should:

- run `scope-reasoner` too if semantic reasoning unknowns also exist
- then run `synthesize`

For the wider route and stage contract, see:

- `../scope-agentic-generation/references/main-flow.md`
- `../scope-agentic-generation/references/stage-contracts.md`
