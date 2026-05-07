# Reasoner Quality Bar

Use this file when preparing `reasoning.input.json`.

The goal is to resolve only the semantic gaps that still need an explicit answer before the next `synthesize` pass.

## What Good Reasoning Looks Like

- resolve only active `semantic_reasoning` unknowns already present in `state.unknowns`
- answer the unknown question directly
- write a non-empty `note` that contains the final result or explicit resolved semantic instruction
- keep the `note` concise and generation-facing
- default to skipping reasoning when synthesis can use the existing constraint directly

## Default Skip Rule

Do less. Reasoning is not a polishing stage.

Skip reasoning when the target is already explicit enough for synthesis, such as:

- `exactly three flags`
- `the red cup is left of the blue bowl`
- `center the subject`
- `use cinematic lighting`
- `make the logo visible`

Use reasoning only when the target contains a hidden value, an ambiguous reference, a required computation, a selection among candidates, or an interpretation of evidence/input imagery.

## Good Cases For Reasoning

Typical reasoning-worthy cases:

- arithmetic or symbolic values that must become visible text
- factual choices that must be selected from evidence
- semantic disambiguation that the next prompt must make explicit
- prompt-level interpretation that cannot be solved by direct retrieval alone

## Resolution Patterns

Keep the output schema as `unknown_id + note`, but make `note` the closed result. Synthesis is responsible for fusing that result with the original prompt and constraints.

- Arithmetic: write the computed value.
  - Good: `The resolved final answer is 45.`
  - Weak: `Calculate 17 + 28 before generation.`
- Visible text: write the exact string.
  - Good: `Use visible text '17 + 28 = 45'.`
  - Weak: `Make the equation correct.`
- Selection: write the chosen item.
  - Good: `Use India, China, and the United States as the three flags.`
  - Weak: `Choose the correct countries.`
- Relation: only reason if the relation is not already explicit.
  - Good: `Place the red cup to the left of the blue bowl.`
  - Skip: if the prompt already says `the red cup is left of the blue bowl`
- Count: only reason if the count is hidden, computed, or selected.
  - Good: `Render exactly three flags side by side.`
  - Skip: if the prompt already says `three flags`
- Layout or viewpoint: only reason when a cue has to be interpreted.
  - Good: `Use a high-angle view looking down toward the intersection.`
  - Skip: if the prompt already says `high-angle view`
- Temporal or ordering logic: write the resolved order or final state.
  - Good: `Show the final scoreboard state after the match ends.`
  - Weak: `Reason about the sequence.`
- Insufficient information: do not invent.
  - Good: `Insufficient information to resolve the exact team score; retrieval is needed.`
  - Weak: `The score is probably 3-2.`

## Cases That Usually Do Not Need Reasoning

- explicit count constraints already fully stated
- explicit relation, layout, or style constraints that can be passed straight to synthesis
- concrete retrieval evidence that can simply be fused into the next prompt
- generic scene wording that does not hide a real semantic gap

## Output Style

Each `reasoning_resolutions[*].note` should be:

- explicit
- resolved
- short
- ready to inject into the next prompt

Good `note` shape:

- `The resolved final answer is 45.`
- `Use the official jersey color as deep royal blue with white trim.`

Weak `note` shape:

- `Think carefully about the arithmetic.`
- `The answer should probably be right.`
- `Consider using the evidence during synthesis.`

## Keep It Tight

- do not restate the entire prompt
- do not output retrieval plans
- do not explain chain-of-thought
- do not describe the reasoning process when the final answer is enough
- do not rewrite the final prompt here
- do not create reasoning notes for targets that are already directly usable

## Done Criteria

The reasoning output is good enough when:

- every `note` resolves a real semantic gap
- each `note` can be copied into synthesis without extra interpretation
- synthesis can fuse the note with the original constraint without doing another round of reasoning
- omitted targets are genuinely better handled by synthesis directly
