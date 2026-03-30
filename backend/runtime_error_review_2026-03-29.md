# 2026-03-29 Dev Runtime Error Review

## Scope

This file records verified failure points observed while running the first full dev digest runtime for business day `2026-03-29`.

The run is still in progress.
This report is being written incrementally to avoid losing intermediate findings.

## Current Runtime Mode

- Initial local dev runner: `backend/app/scripts/dev_run_today_digest_pipeline.py`
- Current long-running mode: `Celery worker + run_daily_coordinator.py`
- Active run id: `4b90237a-7184-43d3-a344-8a986ddacd3f`

## Stage Summary

### Source Collection

Verified state:

- Configured sources completed: `53`
- Current observed source result: all configured sources reached `done`

Current confirmed issue:

- `backend/app/service/news_collection_service.py` emitted `MarkupResemblesLocatorWarning`
- Symptom: a value that looked like a URL was passed into `BeautifulSoup(...)`
- Status: warning only, not a hard stop

## Parse Stage

Latest verified snapshot before this report write:

- `parse_status_counts = {'abandoned': 23, 'done': 675}`
- `retryable_parse_article_count = 0`

Confirmed failure categories:

1. Remote image fetch `403 Forbidden`
2. Non-HTTP image URL handling failure for `data:` URLs

Verified parse failure samples:

- `0be9966c-3cc9-4b2a-bb47-69a4e7f4ff82`
  `ClientResponseError: 403, message='Forbidden', url='https://hips.hearstchina.com/hmg-prod/images/fendi-2026-cny-bff-special-content-still-2-696a05f66c2df.jpg?crop=1.00xw:0.893xh;0,0&resize=1200:*'`
- `17c198ce-a1f3-47f5-9484-58dba7fa1801`
  `ClientResponseError: 403, message='Forbidden', url='https://hips.hearstchina.com/hmg-prod/images/d1200bf8-032e-4ab9-9f99-de5d613ff882.jpg?crop=1.00xw:0.893xh;0,0&resize=1200:*'`
- `322e7856-c87c-4f3f-989f-50442a298803`
  `ClientResponseError: 403, message='Forbidden', url='https://hips.hearstchina.com/hmg-prod/images/bottega-veneta-summer-2026-campaign-16x9-2-6969edef2ecb9.jpg?crop=1.00xw:0.893xh;0,0&resize=1200:*'`
- `818c6922-077d-4fb3-9cee-b6b4742487a9`
  `NonHttpUrlClientError: data:image/gif;base64,R0lGODlhAQABAGAAACH5BAEKAP8ALAAAAAABAAEAAAgEAP8FBAA7`

Observation:

- Parse failures are not caused by missing source collection.
- Markdown writing succeeded for a large portion of the run while a smaller subset failed on image-related requests.

## Event Frame Stage

Latest verified snapshot before this report write:

- `event_frame_status_counts = {'abandoned': 56, 'done': 422, 'pending': 23, 'queued': 183, 'running': 14}`
- `retryable_event_frame_article_count = 0`

Interpretation of current state:

- Event-frame extraction is still the blocking front stage.
- `strict_story` and `digest` have not started yet because event-frame tasks have not drained.
- `pending: 23` matches parse-abandoned articles that will not advance into event extraction.

Confirmed event-frame failure category:

1. Upstream model output fails local schema validation after `model_validate_json(...)`

Verified event-frame failure samples stored in runtime metadata:

- `0283724f-a996-42ac-b6f3-0da684fe693c`
  `ValidationError: 1 validation error for EventFrameExtractionSchema`
  `Input should be an object [type=model_type, input_value='>{', input_type=str]`
- `0453bf97-885c-43a9-a8c7-d6cadc7f9e6e`
  `ValidationError: 1 validation error for EventFrameExtractionSchema`
  `Input should be an object [type=model_type, input_value=0.0, input_type=float]`
- `0e8fdce5-d235-4048-9ddf-3b903028a14e`
  `ValidationError: 1 validation error for EventFrameExtractionSchema`
  `Input should be an object [type=model_type, input_value=1e-308, input_type=float]`

Important limitation:

- Current code persists only `event_frame_error = f"{exc.__class__.__name__}: {exc}"`
- Current code does not persist the raw upstream LLM payload when schema validation fails
- This means the database currently contains wrapped validation errors, not the original upstream response body

## Runtime / Infra Issues Found During This Review

### Issue 1: Local Dev Runner Stops on First Task Failure

Observed behavior:

- `backend/app/scripts/dev_run_today_digest_pipeline.py` runs coordinator in eager mode
- when a single task publish path fails, `_publish_dispatches()` raises the first error immediately
- this prevents the helper script itself from continuing to drain the runtime in one process

Impact:

- This is a dev runner limitation for observation
- This is not the same as the persisted runtime state model
- To continue observing the real chain, the run was switched to `run_celery_worker.py + run_daily_coordinator.py`

### Issue 2: Celery Redis Broker Password Was Not Loaded

Observed behavior before fix:

- Celery worker attempted `redis://localhost:6379/0`
- Redis returned `Authentication required`

Root cause:

- `backend/app/config/celery_config.py` did not load `.env`

Fix applied during this review:

- Added `.env` loading to `backend/app/config/celery_config.py`
- Added test: `backend/tests/test_celery_config.py`

Verification:

- `backend/.venv/bin/python -m unittest backend.tests.test_celery_config`
- result: `OK`
- worker transport after fix: `redis://:**@localhost:6379/0`

## Batch Stages

Final verified terminal snapshot:

- pipeline run status: `done`
- `strict_story.status = done`
- `digest.status = done`
- `strict_story.attempts = 0`
- `digest.attempts = 1`

### Strict Story Stage

Verified result:

- `strict_story` stage started after front stages drained
- `strict_story` stage succeeded on first attempt
- persisted `strict_story` rows for `2026-03-29`: `314`

Observed runtime noise near aggregation boundary:

- worker emitted `Task exception was never retrieved`
- downstream exception text:
  `RuntimeError: Event loop is closed`
- stack trace came from `httpx.AsyncClient.aclose()` during async cleanup

Status:

- this did not block `strict_story` completion
- it is still worth tracking because it indicates async client cleanup is happening after loop shutdown

### Digest Stage

Verified result:

- first `digest` generation attempt failed
- second `digest` generation attempt succeeded
- runtime marked digest batch stage as `done`

Verified first-attempt digest failure:

- exception type: `ValidationError`
- schema: `DigestGenerationSchema`
- message:
  `Input should be an object [type=model_type, input_value='>{', input_type=str]`

Important functional finding:

- final `digest` table row count is still `0`
- query by business day `2026-03-29` returned no digest rows
- query for latest 10 digests also returned no rows

Why this matters:

- runtime terminal status says `done`
- digest batch stage says `done`
- but public read model `digest` is empty

Code-path observation:

- `DigestGenerationService._select_digest_plans()` returns `DigestGenerationSchema()`
  when no plans are produced
- `_replace_day_digests(..., plans=[])` deletes old day rows and inserts none
- this means `digest stage done` does not currently imply `digest rows persisted > 0`

Current assessment:

- this is a real functional error point for the first full dev chain
- even though aggregation task returned success, the public read model was not produced

## Final Stage Counts

Final verified counts:

- `parse_status_counts = {'abandoned': 23, 'done': 675}`
- `event_frame_status_counts = {'abandoned': 127, 'done': 548, 'pending': 23}`
- `source_status_counts = {'done': 53}`

Interpretation:

- source collection fully completed
- parse stage fully drained
- event-frame stage fully drained for parse-complete articles
- `pending: 23` in event-frame corresponds to parse-abandoned articles and no longer blocks aggregation

## Final Outcome

Verified end state of the first full dev runtime:

1. Source collection completed.
2. Parse completed with a bounded set of permanent failures.
3. Event-frame extraction completed with a larger set of permanent schema-validation failures.
4. `strict_story` generation completed and persisted rows.
5. `digest` generation reached terminal success after one retry but did not persist any `digest` rows.

## Follow-Up Targets

Most important follow-up items based on this run:

1. Persist raw upstream LLM payload for event-frame validation failures.
2. Persist raw upstream LLM payload for digest-generation validation failures.
3. Decide whether image fetch failures during parse should fail the whole article or degrade without blocking markdown persistence.
4. Decide whether `digest stage done && digest_count == 0` should be treated as failure instead of success.
5. Investigate async client cleanup path that logs `RuntimeError: Event loop is closed`.
