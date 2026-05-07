# Retriever Quality Bar

Use this file when preparing `retrieval.input.json`.

The goal is not to summarize everything you know. The goal is to produce only the external grounding notes that will materially improve the next `synthesize` pass.

## What Good Retrieval Looks Like

- retrieve only for active `external_reference` unknowns already present in `state.unknowns`
- write a short owner-aligned query plan before running search
- keep each note concrete and generation-facing
- cite the evidence used for each resolution when web search is available
- prefer task-defining named anchors over generic scene parts
- write notes that a synthesizer can directly fold into the next prompt
- keep the note set small; prefer a few high-value notes over broad evidence dumps

## Query Quality

Good retrieval starts with query planning.

Each query should be:

- short
- search-engine-native
- anchored to the unknown owner
- free of generation verbs
- free of generic filler like `appearance`, `picture`, `image`, or `visual reference`

Use `text_queries` for facts, dates, names, values, or data that need textual grounding.
Use `image_queries` for visual identity, product appearance, logos, landmarks, or other visual anchors.

For people, characters, public figures, brands, products, logos, and named IPs, use at most 2 image query variants.

Good query plan shape:

- `image_queries`: `["MrBeast portrait", "MrBeast official photo"]`
- `image_queries`: `["Coca-Cola can official", "Coca-Cola can front view"]`
- `text_queries`: `["2025 NBA Finals final score"]`

Weak query plan shape:

- `image_queries`: `["Create an image of MrBeast in a studio"]`
- `image_queries`: `["what does the product look like visual reference appearance"]`
- `text_queries`: `["information about this prompt"]`

## Good Targets

Typical good retrieval targets:

- named people, characters, or public figures whose visual identity matters
- named brands, products, landmarks, artworks, vehicles, or logos whose appearance is task-defining
- explicit official or real-world facts that change whether the generated image is semantically correct
- reference-image preservation requirements that need concrete identity or appearance grounding

## Weak Targets

Usually do not retrieve for:

- generic objects like `man`, `plate`, `croissant`, `tree`
- ordinary subordinate scene parts already covered by a higher-level named anchor
- constraints that are already explicit and visually executable without outside evidence
- prompt-level semantic gaps that really need reasoning rather than search

## Output Style

Each `retrieval_resolutions[*].note` should be:

- short
- concrete
- owner-aligned
- visually or factually actionable

Each `retrieval_resolutions[*].evidence` item should be:

- tied to the same unknown as the note
- source-specific, not generic
- short enough for audit and synthesis
- preferably from official, primary, or stable reference pages

Good note shape:

- `Use the official red Coca-Cola can reference with classic white script and standard slim can proportions.`
- `Preserve the Eiffel Tower exterior silhouette and lattice structure from the official landmark appearance.`

Weak note shape:

- `Look up more information about the object.`
- `Use references from the internet.`
- `The subject should be accurate.`

## Keep It Tight

- do not paste the full prompt into search queries
- do not restate the whole prompt
- do not explain the search process
- do not dump search-engine query variants into `retrieval_resolutions[*].note`
- do not write reasoning conclusions here
- do not rewrite the final prompt here

## Done Criteria

The retrieval output is good enough when:

- every note answers a real `external_reference` gap
- removing the note would plausibly make the next image less semantically grounded
- the notes are concrete enough for `synthesize` to use directly
