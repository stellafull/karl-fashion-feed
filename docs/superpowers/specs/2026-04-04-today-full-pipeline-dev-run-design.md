# Today Full Pipeline Dev Run Design

## Goal

Add one dev-only script that runs a real end-to-end backend pipeline once:

- collect live source articles
- deduplicate into `article`
- parse markdown and images
- extract `article_event_frame`
- cluster `story`
- generate `digest`
- upsert retrieval units into Qdrant

The run is scoped to articles whose final `published_at` falls on the current Asia/Shanghai business day.

## Why A New Script

The existing [backend/app/scripts/dev_run_today_digest_pipeline.py](/home/czy/karl-fashion-feed/backend/app/scripts/dev_run_today_digest_pipeline.py) is built around the current coordinator runtime and review output. It is not the right entrypoint for this task because:

- it is digest-oriented, not full RAG completion
- the coordinator rescans runtime state and can pick up unrelated pending rows from the dev database
- this task needs one explicit, synchronous, one-off execution over a precisely bounded article set

## Chosen Approach

Create a new synchronous script under [backend/app/scripts](/home/czy/karl-fashion-feed/backend/app/scripts):

- run collection directly through `ArticleCollectionService`
- parse only the newly inserted article IDs from this collection run
- determine eligibility after parse using final `published_at`
- extract event frames only for eligible article IDs
- create one dedicated `PipelineRun` row for this dev run
- call story clustering and digest generation directly
- upsert only the eligible article IDs into RAG
- emit one review bundle summarizing the exact article set and downstream results

This keeps the path short and avoids compatibility layers, coordinator state coupling, or partial fallback behavior.

## Hard Preconditions

The script must fail fast if any of these are false:

- Postgres is reachable
- Qdrant is reachable
- the configured LLM and embedding credentials are available
- the current Asia/Shanghai business day has no pre-existing `article_event_frame` rows
- the current Asia/Shanghai business day has no pre-existing `story` rows
- the current Asia/Shanghai business day has no pre-existing `digest` rows

The last three checks are required because current clustering and digest generation are business-day wide, not run-scoped. Silent cleanup is not allowed.

## Runtime Flow

### 1. Bootstrap

Resolve:

- `now` in UTC
- current Asia/Shanghai `business_day`
- optional source filters
- optional artifact output directory
- optional `KARL_LLM_DEBUG_ARTIFACT_DIR`

Ensure runtime schema exists before any stage runs.

### 2. Assert Clean Business Day

Before any aggregation-stage work, query current-day counts for:

- `article_event_frame`
- `story`
- `digest`

If any count is non-zero, raise immediately with the exact counts.

### 3. Collect And Deduplicate

Run live collection through `ArticleCollectionService.collect_articles(...)`.

The script should use the real source configuration and store real results into the dev database. It should then rely on `inserted_article_ids` from the collection result as the only upstream article set for this run.

If `inserted == 0`, raise immediately. A zero-insert run does not validate the pipeline.

### 4. Parse New Articles

Run `ArticleParseService.parse_articles(article_ids=inserted_article_ids)`.

This stage parses every newly inserted article, not just feed-level `published_at=today` rows, because some sources only reveal a reliable publication timestamp after detail-page parsing.

If any inserted article fails parse, raise immediately with the failed count and article IDs. This is a real pipeline test, not a best-effort review flow.

### 5. Resolve Today-Eligible Articles

Reload the inserted article rows after parse and keep only articles that satisfy all of the following:

- `parse_status == "done"`
- `published_at` is not null
- `published_at` falls within the current Asia/Shanghai business-day UTC bounds

If the eligible set is empty, raise immediately.

This eligible set becomes the only downstream truth set for event-frame extraction, digest generation, and RAG upsert.

### 6. Extract Event Frames

Run event-frame extraction only for the eligible article IDs.

Every eligible article must end with `event_frame_status == "done"`. Any failure aborts the run immediately.

### 7. Create Dedicated Dev Pipeline Run

Insert one `PipelineRun` row with a dev-only `run_type`, for example `dev_today_full_pipeline`.

This row exists only to satisfy `created_run_id` foreign keys and to make the review bundle traceable to one explicit run.

The script should not reuse the coordinator’s `digest_daily` run type.

### 8. Generate Story And Digest

Call:

- `StoryClusteringService.cluster_business_day(...)`
- `DigestGenerationService.generate_for_day(...)`

using the current `business_day` and the dedicated dev `run_id`.

Because the script already asserted the business day is clean and only eligible articles received event frames, the aggregation result is bounded to this run without adding compatibility filters into core services.

If event frames exist but no stories are produced, raise immediately.
If stories exist but no digests are produced, raise immediately.

### 9. Upsert RAG

Run `ArticleRagService.upsert_articles(eligible_article_ids)`.

Only the eligible same-day article IDs should be inserted into Qdrant for this dev run. Parsed-but-not-eligible articles must not be indexed by this script.

If the eligible set is non-empty but zero retrieval units are upserted, raise immediately.

### 10. Write Review Bundle

Write one review bundle under `backend/runtime_reviews/` by default.

The bundle should include at least:

- run metadata
- collection counts
- inserted article IDs
- eligible article IDs
- parse/event-frame/story/digest/RAG counts
- generated digest keys
- any source filters used

This output is the handoff artifact for frontend-backend联调 readiness review.

## Script Interface

Keep the CLI small and directly useful for debugging:

- `--source-name` repeatable
- `--limit-sources`
- `--output-dir`
- `--llm-artifact-dir`

No compatibility flags, no fallback cleanup flags, and no alternate runtime modes.

## Error Handling

This script is not a resilient production scheduler. It is a dev validation tool.

Rules:

- fail fast on any unmet precondition
- fail fast on any parse failure
- fail fast on any eligible article event-frame failure
- fail fast on empty downstream artifacts where non-empty input exists
- always print the failing stage and exact counts/IDs needed to debug

## Testing

Add focused unit tests around the new script’s deterministic behavior:

- CLI parser accepts the supported flags
- business-day eligibility filter uses Asia/Shanghai bounds correctly
- clean-business-day assertion raises when current-day frame/story/digest rows already exist
- review bundle writing includes the required summary fields

Do not attempt a mocked full-network integration test in unit test scope.

## Non-Goals

- no coordinator changes
- no Celery execution changes
- no patch to make clustering run-scoped
- no cleanup helper for old business-day data
- no compatibility path for old `backend/scripts` runtime

## Implementation Target

Primary changes should land in:

- `backend/app/scripts/dev_run_today_full_pipeline.py`
- `backend/tests/test_dev_run_today_full_pipeline.py`
- `backend/app/scripts/README.md`

The script should reuse existing services rather than adding new orchestration layers.
