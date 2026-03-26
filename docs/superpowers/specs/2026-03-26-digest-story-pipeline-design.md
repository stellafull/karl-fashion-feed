# Digest Story Pipeline Redesign

## Summary

This spec redesigns the current story pipeline into a layered content model:

`article -> article_event_frame -> strict_story -> digest`

The redesign separates two different decisions that are currently mixed together:

- `same event?`
- `same card?`

`strict_story` answers `same event?` and exists only as a lightweight internal event packing layer.
`digest` answers `same card?` and becomes the only public read model for feed and detail reading.

This spec covers:

- content production model
- daily pipeline stages
- persisted read model for `digest`
- feed/detail API shape at the `digest` layer

This spec does not cover:

- runtime execution architecture
- queueing, worker isolation, Redis orchestration
- near-duplicate detection strategy such as SimHash/MinHash
- chat agent implementation details
- frontend visual redesign

## Goals

- Make the content pipeline align with the actual product semantics.
- Preserve `article` as the truth source.
- Introduce a sparse, replayable event layer before digest generation.
- Reduce digest merge search space by compressing article-level facts into event units first.
- Make `digest` the only public reading object.
- Persist complete digest article bodies during the daily pipeline.
- Keep same-day reruns able to reuse stable identities.
- Support partial failure without blocking the whole day’s output.

## Non-Goals

- No cross-day event continuity.
- No historical snapshot/version retention for same-day reruns.
- No attempt to preserve the current `story` read model name or semantics.
- No pre-generated QA context pack.
- No scoped retrieval limited to a digest’s source set.

## Product Semantics

### `article`

`article` remains the only primary truth source.

- Deduplication remains anchored on normalized `canonical_url`.
- Article body and image assets remain stored as first-class source material.
- `article` no longer owns the primary publish decision for the new pipeline.
- `article` no longer directly represents a reader-facing story unit.

For replay and same-day rerun determinism, the normalized article materials used by downstream extraction and digest writing must be durable.
They may live on `article` itself or in a companion storage layer, but they must not exist only as transient prompt inputs.

### `article_event_frame`

`article_event_frame` is the new minimum orchestration unit.

- It is extracted from a single normalized article.
- It must be persisted and replayable.
- Each article may produce `0..3` high-confidence frames only.
- Frame extraction must be intentionally sparse.
- `event_type` may remain open-text, but it is descriptive only.
- `event_type` is not a hard identity or grouping key.

Each frame must carry enough structure to support replay and event grouping:

- business date
- source `article_id`
- core subject/entity
- action or change description
- object/target where applicable
- time window
- place, collection, season, or show context where applicable
- evidence excerpt references back to the source article
- confidence and extraction diagnostics

### `strict_story`

`strict_story` is a lightweight internal event packing layer.

It exists to compress many article-level event frames into fewer stable event units before digest generation.

It is not a public reading object and not a second article-like report.

Its responsibilities are limited to:

- grouping event frames that describe the same event
- assigning a stable `strict_story_key` for same-day reruns
- storing a very short event synopsis
- storing membership relations

Within one business day:

- one `article_event_frame` belongs to exactly one `strict_story`
- one `strict_story` may include frames from multiple articles
- one article may appear in multiple `strict_story` objects because it may yield multiple frames

### `digest`

`digest` is the only public reading object.

It is:

- a reader-facing Chinese report
- bound to exactly one facet
- persisted with complete article body content during the daily pipeline
- the object used by feed and detail APIs

`digest` is not required to consume every `strict_story`.

Allowed shapes:

- `1 strict_story -> 1 digest`
- `N strict_story -> 1 digest`

A `strict_story` may belong to multiple digests, but a later digest must add new combination value.
It cannot be only a rewritten version of the same strict-story membership set.

## Business Date

All grouping in this spec is defined by business day using:

- `ingested_at`
- interpreted in `Asia/Shanghai`
- natural calendar day boundaries

The system does not backfill late-arriving content into an earlier day.

## Identity Rules

### `strict_story_key`

`strict_story_key` should be stable across same-day reruns.

Reuse rule:

1. build candidates from matching or near-matching event signatures
2. compare frame membership overlap
3. use LLM review only as a tie-breaker or ambiguity resolver

The stable identity judgment is therefore mixed:

- signature first
- membership overlap second
- model review third

### `digest_key`

`digest_key` should be stable across same-day reruns.

Primary reuse rule:

- same facet
- same or nearly same `strict_story` membership set

Body text, title, and deck may change on rerun.
Identity should prefer member-set stability over wording stability.

### Rerun Version Policy

Same-day reruns do not preserve historical versions.

The pipeline should:

- reuse `strict_story_key` where possible
- reuse `digest_key` where possible
- overwrite the current stored state for that business day

No version history or snapshot lineage is required in this redesign.
Objects from an earlier same-day run that are not produced by the rerun should be removed from current state rather than retained as inactive historical rows.

## Data Model

This spec intentionally defines data model boundaries without requiring a final column-by-column migration in the spec itself.

### Existing Truth Tables Retained

- `article`
- `article_image`

### New Internal Tables

- `article_event_frame`
  - persisted sparse event frames
- `strict_story`
  - internal lightweight event unit
- `strict_story_frame`
  - membership relation from `strict_story` to `article_event_frame`
- `strict_story_article`
  - derived relation from `strict_story` to source `article`

### New Public Read Tables

- `digest`
  - public reader-facing digest object
- `digest_strict_story`
  - membership relation from `digest` to `strict_story`
- `digest_article`
  - flattened evidence/source relation from `digest` to source `article`

### Required Stored Fields by Object

#### `article_event_frame`

Minimum required fields:

- `event_frame_id`
- `article_id`
- `business_date`
- descriptive `event_type`
- subject/entity fields
- action/object fields
- time window fields
- place/collection/season fields where present
- `signature_json` or equivalent normalized event identity payload
- short evidence excerpt or excerpt locator payload
- extraction confidence
- extraction status/error metadata

#### `strict_story`

Minimum required fields:

- `strict_story_key`
- `business_date`
- normalized event signature payload
- short synopsis text
- aggregate confidence or review metadata
- status/error metadata for packing

#### `digest`

Minimum required fields:

- `digest_key`
- `business_date`
- `facet`
- `title_zh`
- `dek_zh`
- `body_markdown`
- `hero_image_url`
- `source_article_count`
- `source_names_json`
- `created_run_id`
- generation status/error metadata

## Daily Pipeline

### Stage 1: Article Intake

Input:

- discovered content from sources

Output:

- persisted `article`
- persisted `article_image`
- parsed source body materials

Responsibilities:

- collect source pages
- normalize canonical URL
- deduplicate by canonical URL
- parse article content and assets
- persist truth-source records first

This stage does not decide final reader publication.

### Stage 2: Article Normalization

Input:

- persisted parsed article

Output:

- normalized Chinese reading material for downstream extraction and writing

Responsibilities:

- normalize multilingual content into stable downstream inputs
- produce translated title/summary/body materials suitable for extraction and writing

This stage is not the primary publish gate.
Legacy `article.should_publish` must not remain the main decision point in the redesigned pipeline.
The output of this stage must be persisted or otherwise made deterministically reproducible for replay and same-day reruns.

### Stage 3: Event Frame Extraction

Input:

- one normalized article

Output:

- `0..3` persisted `article_event_frame`

Rules:

- extraction must be sparse, not exhaustive
- only high-confidence frames are kept
- no frame is acceptable if the article yields no worthwhile event unit

Failure semantics:

- failure affects only the article being processed
- extraction failure does not fail the full daily run

### Stage 4: Strict Story Packing

Input:

- current business day event frames

Output:

- packed `strict_story` units
- `strict_story_frame` relations
- `strict_story_article` relations

Responsibilities:

- answer `same event?`
- compress many event frames into fewer event units
- create short event synopses only
- assign or reuse `strict_story_key`

This stage is intentionally lightweight.
It does not produce a public report body.

### Stage 5: Digest Generation

Input:

- current business day `strict_story` units
- their backing source articles and normalized article materials

Output:

- selected public `digest` objects
- `digest_strict_story` relations
- `digest_article` relations

Responsibilities:

- answer `same card?`
- bind one facet per digest
- choose whether a strict story should be published at all
- generate a full reader-facing Chinese article body

Facet assignment happens at digest generation time.
`strict_story` does not require any precomputed facet identity in order to participate in digest generation.

Rules:

- not every `strict_story` must become a digest
- a `digest` may contain one or more `strict_story` members
- a `strict_story` may belong to more than one digest only if the later digest adds new strict-story combination value
- a digest cannot be only a reworded duplicate of the same strict-story membership set

### Stage 6: Digest Persistence

Input:

- generated digest result

Output:

- persisted `digest`
- persisted digest relations to strict stories and articles

This stage creates the public read model consumed by the frontend.

## Publication Filter Semantics

Publication judgment moves down from `article` to the event and digest layers.

Interpretation:

- all articles should still be collected and stored
- only extracted frames advance into the event layer
- only selected strict-story combinations become digests

Reasons a `strict_story` may never become a `digest` include:

- weak event importance
- insufficient article value to support a useful report
- no natural facet or composition that yields a strong reading object

The system should support these as combined judgment factors, not a single hard reason code.

## Failure Semantics

The redesigned pipeline is partial-success by default.

Allowed behavior:

- one article normalization failure does not block other articles
- one event frame extraction failure does not block other frames
- one strict-story packing failure does not block other strict stories
- one digest generation failure does not block other digests

The daily pipeline should fail as a whole only when orchestration itself cannot complete the run.

`pipeline_run` must record object-level counts and failure summaries rather than only a single run-level success flag.

## Replay and Observability

The system must support direct replay and inspection of:

`article -> article_event_frame -> strict_story -> digest`

Required observability outcomes:

- inspect why an article yielded zero frames
- inspect which frame joined which strict story
- inspect which strict stories were selected into which digest
- inspect why a digest was not produced or failed generation

This replayability requirement is a primary reason to persist `article_event_frame` and `strict_story` explicitly.

## Read Model and API Contract

### Public Object

The public object is `digest`, not `strict_story`.

The existing `story` read model semantics should be replaced rather than extended.

### Feed API

Feed returns digest cards only.

Each card should expose only reader-facing fields needed by the homepage:

- `id`
- `facet`
- `title`
- `dek`
- `image`
- `published`
- `article_count`
- `source_count`
- `source_names`

The feed API must not expose internal strict-story structure.

Overlapping digests are allowed in the feed if they are legitimately different public reading objects.

### Detail API

Detail returns one full digest reading object.

Required fields:

- `id`
- `facet`
- `title`
- `dek`
- `body_markdown`
- `hero_image`
- `published`
- flattened source/reference list

Reference/source list should resolve to original article webpages and article metadata.

The detail API must not expose `strict_story` as a first-class navigable object.

## QA Downstream Contract

QA runtime is out of implementation scope for this spec, but the digest model must support the following downstream contract.

### Role of Digest in Follow-up

`digest` is a narrative starting point, not a retrieval boundary and not a final evidence source.

### Query Understanding

A follow-up query should first go through model-driven query understanding and rewrite using:

- digest context
- user query
- session history

### Retrieval Contract

The default evidence pool is the full corpus of collected webpage-derived article chunks and image evidence.

The current digest does not scope retrieval.

The downstream agent may choose among:

- corpus RAG
- image retrieval
- web search
- web fetch

### Answering Contract

The answer target is the most credible current answer, not merely the answer bounded by the current digest.

The agent may:

- broaden beyond the digest
- update or correct digest framing
- incorporate newer or broader evidence

### Citation Contract

User-visible citations should resolve to source webpages.

`digest` body text itself is not a citation source.

## Scope Boundaries for Planning

This spec is intentionally limited to one planning scope:

- content production model
- digest read model
- feed/detail API contract

The following are explicitly excluded from the implementation plan created from this spec:

- ingestion concurrency model
- queue architecture
- Redis responsibilities
- retry worker topology
- memory/backpressure controls
- near-duplicate content strategy such as SimHash/MinHash
- agent runtime orchestration
- external web tool execution implementation
- frontend design changes

Those belong in separate runtime and retrieval-agent specs.

## Migration Direction

The target state after this redesign is:

- `story` no longer acts as the public semantic model
- `digest` becomes the public read model
- `strict_story` exists only as an internal event layer
- `article` remains the truth source

Planning should treat this as a semantic replacement, not a compatibility patch layered onto the current `story` model.
