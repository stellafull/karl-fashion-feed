# Story-Digest Runtime Redesign

## Summary

This spec redesigns the current content runtime into a four-layer model:

`article -> event_frame -> story -> digest`

The redesign fixes a semantic mismatch in the current pipeline:

- `event_frame` should be the machine-readable local fact unit extracted from one article
- `story` should be the same-day canonical event aggregated across sources and angles
- `digest` should be the reader-facing long-form output shaped by editorial intent

The current runtime collapses `story` into deterministic buckets derived from single-article extraction output.
That makes `strict_story` an implementation artifact instead of a real event-clustering layer.
This redesign restores `story` as an explicit cross-frame aggregation stage and moves `digest` back to its correct role as editorial packaging plus final report writing.

This spec covers:

- target content semantics
- same-day event clustering strategy
- facet assignment and digest packaging
- digest report writing
- data model changes
- failure semantics
- observability and debug artifacts

This spec does not cover:

- exact queue topology or worker deployment
- frontend implementation details
- cross-day topic/thread stitching
- image understanding beyond current article/image truth-source handling
- exact prompt wording

## Goals

- Preserve `article` as the truth source.
- Keep `event_frame` as the smallest replayable machine abstraction.
- Introduce a real `story` clustering stage for same-day event aggregation.
- Allow the same `story` to appear in multiple digests, including within the same facet.
- Make `digest` the final user-facing long-form Chinese reading object.
- Limit full-article LLM context to the final digest writing stage only.
- Keep development debugging cheap by using narrow script-level sampling, not production branching.
- Expose raw errors and raw LLM I/O for reliable debugging.

## Non-Goals

- No cross-day `story` continuity.
- No requirement that every `story` must appear in a `digest`.
- No requirement that `digest` objects partition the set of stories.
- No paragraph-level source attribution inside `digest.body_markdown`.
- No fallback behavior that silently broadens dev debug scope.

## Confirmed Product Semantics

### `article`

`article` remains the raw material.

- It is the truth source.
- It is deduplicated by normalized `canonical_url`.
- It is not the primary user reading object.
- Its parsed markdown and image materials remain durable inputs for downstream stages.

### `event_frame`

`event_frame` is the machine layer.

- It is extracted from one article.
- One article may yield `0..N` event frames.
- It exists to identify high-confidence local event facts such as who, what, where, when, and what changed.
- It is replayable and persisted.

`event_frame` does not define final `story` membership.

`signature_json` is outside the redesigned runtime semantics.
Story construction must not depend on it.

### `story`

`story` is the same-day canonical event.

- It aggregates multiple near-duplicate or angle-shifted `event_frame` objects that refer to the same underlying event.
- It is scoped to one business day only.
- It is not a public reading model.
- It is the canonical aggregation layer between extraction and editorial packaging.

`story` clustering is intentionally merge-biased:

- if multiple frames likely describe the same event, the system should prefer merging over over-fragmenting
- however, merging must still be bounded by explicit consistency checks to avoid runaway chain-merges

`story` is facet-neutral.

- It does not belong to exactly one facet.
- Its facet usage is determined downstream by editorial packaging.

### `digest`

`digest` is the user reading unit.

- It is a long-form Chinese report, not a short summary card.
- It is shaped by editorial intent.
- It belongs to exactly one facet.
- It may reference one or many stories.
- The same story may appear in multiple digests.
- Not every story must appear in a digest.

The only product facets are:

- `runway_series`
- `street_style`
- `trend_summary`
- `brand_market`

`all` is a read-layer aggregation view only.
It is not a persisted facet and not a fifth digest type.

## Current Runtime Problems

The redesign exists because the current runtime has several structural problems.

### Problem 1: `strict_story` is not real clustering

The current packing stage groups frames by deterministic serialization of:

- `event_type`
- `signature_json`

This is precise bucketing, not same-event clustering.
As a result:

- slightly different signatures force splits
- overly broad signatures force merges
- cross-source event alignment is decided too early and too locally

### Problem 2: grouping semantics are delegated to single-article extraction

Single-article extraction has only local article context.
It cannot reliably produce a cross-document canonical aggregation key for same-event clustering.

### Problem 3: same-day key stability depends on unstable frame IDs

The current rerun reuse logic depends on frame membership overlap, while frame extraction recreates rows during rerun.
That makes same-day key stability fragile.

### Problem 4: current digest generation behaves like a hard assignment

The current digest stage constrains one `strict_story` to one digest during one run.
That conflicts with the product requirement that one canonical event may support multiple editorial readings.

### Problem 5: current digest generation is too summary-oriented

Current `digest.body_markdown` generation is effectively a concise editorial summary.
The target product object is a longer reported article.

### Problem 6: current runtime observability is insufficient

Errors and LLM behavior must be inspectable without reverse-engineering helper layers.
Raw prompt/response capture is required in dev debugging workflows.

## Business Day

Production business-day semantics remain unchanged:

- anchored on `article.ingested_at`
- interpreted in `Asia/Shanghai`
- grouped by natural calendar day

Development debug scope must not modify production semantics.

If a developer wants to run a narrow local simulation, that belongs only in `backend/app/scripts/` and may use:

- `published_at` within the current local day

In that dev-only scope:

- `published_at is null` must be excluded
- no fallback to `ingested_at` is allowed

This preserves fail-fast semantics and keeps production runtime definitions clean.

## Target Architecture

The redesigned runtime is:

`article -> event_frame -> story clustering -> facet assignment -> digest packaging -> digest report writing`

### Stage 1: Article collection and parse

No semantic change.

The system continues to:

- collect new articles
- deduplicate on normalized `canonical_url`
- persist article metadata
- parse and persist markdown plus image truth-source material

### Stage 2: Event-frame extraction

The extraction stage is reused with narrowed responsibility.

It should continue to output sparse, replayable event frames with:

- structured entities and anchors
- action/change description
- confidence
- evidence snippets

But it must no longer be treated as the place where final event grouping is decided.

### Stage 3: Story clustering

This becomes a new explicit same-day aggregation stage.

Its job is to produce canonical same-day events from many article-local frames.

#### Inputs

- all event frames for the business day
- article/source metadata for those frames

#### Output objects

- `story`
- `story_frame`
- `story_article`

#### Clustering strategy

The clustering strategy must not depend on global all-to-all LLM reasoning.
Instead, it should use bounded-context graph construction.

1. Build a compact `frame card` for each event frame.
   A frame card contains compact clustering signals only:
   - event type
   - brand/person/collection/season/place/time anchors
   - action summary
   - compact evidence snippets
   - article/source identifiers

2. Generate candidate neighbors with blocking.
   Blocking should use lightweight structured anchors and only optional semantic recall.
   Examples:
   - same brand
   - same person
   - same collection
   - same place
   - overlapping time window
   - embedding nearest neighbors as supplemental recall only

3. Run small-window LLM judgments on local candidate sets.
   The LLM does not perform global clustering.
   It only judges whether a small group of frame cards refer to the same event.

4. Build a merge graph from accepted local same-event judgments.

5. Run cluster-level consistency review.
   This step prevents chain-merging pathologies such as:
   - `A` matches `B`
   - `B` matches `C`
   - but `A` and `C` should not land in one story

6. Emit final story clusters and canonical story metadata.

#### Event type handling

`event_type` is a weak clustering signal only.

- different frame-level event types may still land in the same story
- final canonical `story.event_type` is determined after clustering, not inherited as a hard upstream truth

#### Identity stability

Same-day `story_key` reuse must not depend primarily on ephemeral frame row IDs.

Reuse should instead prefer stable cluster features such as:

- article membership
- canonical anchor set
- cluster content fingerprint

No cross-day reuse is required.

### Stage 4: Facet assignment

Facet assignment is a separate stage between clustering and digest packaging.

Its job is to determine whether a story is editorially relevant to one or more product facets.

Properties:

- input is `story`, not raw article markdown
- output is `story -> 0..N facets`
- a story may belong to zero facets
- a story may belong to multiple facets

This stage should not write final digest copy.
It only produces membership decisions.

### Stage 5: Digest packaging

Digest packaging is a facet-local editorial packaging stage.

It does not partition stories.
Instead, it creates one or more candidate reading packages inside each facet.

#### Inputs

- stories assigned to a facet
- compact `story cards`

Each story card should contain compact editorial signals only, for example:

- canonical synopsis
- canonical anchors
- representative sources
- source count
- article IDs
- canonical event type

#### Packaging strategy

Digest packaging must use bounded windows.
It must not feed the full same-day story set to one LLM call.

Recommended approach:

1. Generate package candidates with lightweight heuristics.
   Examples:
   - shared brand
   - shared designer/person
   - shared collection or season
   - shared market move
   - shared trend signal

2. For each facet, run local packaging review on small candidate groups.

3. Produce one or more `digest plans`.

A `digest plan` should contain:

- facet
- selected story keys
- selected article IDs
- editorial angle
- title seed/dek seed if needed for writing

#### Story reuse

Digest packaging explicitly allows overlap.

- the same story may appear in multiple digests
- this is allowed across facets
- this is also allowed within the same facet

#### `trend_summary` behavior

`trend_summary` should be biased toward multi-story horizontal synthesis.

It may still produce a smaller package when the day is sparse, but the default editorial behavior should prefer cross-story pattern extraction over rephrasing one event.

#### Package size limits

Each digest plan should have explicit hard limits such as:

- maximum stories per digest
- maximum source articles per digest

If a candidate package exceeds the limit, it must be split into multiple digest plans.

The exact numeric limits belong in implementation planning rather than this spec, but hard limits are required for cost control.

### Stage 6: Digest report writing

Only this final stage may load full source article text.

Its job is to convert one approved digest plan into one reader-facing long-form report.

#### Inputs

- one digest plan
- selected source article markdown
- source names
- canonical URLs

#### Output

- one `digest` row
- one continuous long-form Chinese report in `body_markdown`

This body should read like a reported article, not a short summary bundle.

The model may cite original canonical URLs inside the article body.
However, the system only requires coarse-grained persisted provenance:

- source story membership
- source article membership

Paragraph-level source mapping is not required.

## Data Model

### Naming

The redesign should stop using `strict_story` as the semantic name for the canonical event layer.
The target layer is `story`.

This spec defines the target runtime model as:

- `story`
- `story_frame`
- `story_article`
- `story_facet`
- `digest`
- `digest_story`
- `digest_article`

### `story`

Minimum required semantics:

- same-day canonical event aggregate
- stable same-day `story_key`
- canonical synopsis
- canonical event type
- canonical anchor payload
- business day
- stage status and error fields

### `story_frame`

Ordered membership from story to event frame.

Each event frame belongs to exactly one final story for the business day.

### `story_article`

Derived membership from story to source article.

One story may reference many source articles.
One source article may appear in many stories because one article may yield many frames.

### `story_facet`

Explicit membership from story to facet.

This table records positive assignment only.
No negative audit table is required.

### `digest`

Reader-facing long-form output with:

- `digest_key`
- `business_date`
- `facet`
- `title_zh`
- `dek_zh`
- `body_markdown`
- source article count
- source names
- stage status and error fields

### `digest_story`

Many-to-many membership from digest to story.

This table must allow the same story to be referenced by multiple digests.

### `digest_article`

Flattened many-to-many membership from digest to article.

This preserves coarse-grained provenance and supports later review or audit.

## Failure Semantics

The redesigned runtime must fail fast and visibly.

### Story clustering failures

If the stage has usable event-frame input for the business day but produces zero stories, the run must fail.

### Facet assignment outcomes

If all stories receive zero facet assignments, that is allowed.
This is an editorial output, not necessarily a runtime failure.

### Digest packaging failures

If a facet has eligible stories for packaging but packaging produces zero digest plans for that facet, the run must fail for that facet.

If the full day has eligible packaging input but produces zero digest plans overall, the run must fail.

### Digest writing failures

If any digest plan reaches report writing and writing fails, the run must fail.
The system must not mark the run done while producing an empty final digest set.

### No silent fallbacks

The runtime must not hide missing inputs or broaden scope silently.
If a stage receives malformed or insufficient input, it should fail in place with raw diagnostics.

## Observability and Debugging

Observability is a first-class requirement.

### Structured logs

Each stage must produce structured logs with enough identifiers to trace one object through the pipeline.

Minimum fields:

- `run_id`
- `business_day`
- `stage`
- object key such as `article_id`, `story_key`, or `digest_key`

### Raw exceptions

Error records must preserve:

- exception class
- original error message
- full traceback

The system must avoid wrapper patterns that discard root-cause detail.

### Dev debug artifacts

Local debug scripts must write raw LLM artifacts to disk.

Required behavior in dev debugging:

- persist each raw prompt
- persist each raw response
- group artifacts by `run_id`, stage, and object identifier

This requirement applies to script-based debugging flows.
It does not require production runtime to persist raw LLM I/O by default.

### Debug scope location

Any narrow dev-only sampling logic such as `published_at=today` must live in scripts only.

It must not become a production coordinator mode, runtime branch, or general-purpose fallback path.

## Recommended Runtime Principles

- Use small-window LLM reasoning for clustering and packaging.
- Use lightweight blocking to constrain local comparison sets.
- Use full article markdown only in final digest report writing.
- Prefer merge-biased story clustering, then clean up with cluster-level consistency checks.
- Keep production runtime semantics singular and clean.
- Keep debugging affordances script-local and explicit.

## Acceptance Criteria

The redesign is complete only if all of the following are true:

- `story` is a real explicit clustering layer and is no longer derived from deterministic `signature_json` bucketing
- the same story can appear in multiple digests
- digest packaging operates on compact story representations rather than full raw article text
- digest writing is the only stage that loads full article markdown
- digest output is a long-form reported article rather than only a short editorial summary
- dev-only sampling logic exists only in scripts
- debug scripts persist raw prompt/response artifacts
- the runtime cannot report success while producing an unexpectedly empty final digest set from non-empty eligible upstream input
