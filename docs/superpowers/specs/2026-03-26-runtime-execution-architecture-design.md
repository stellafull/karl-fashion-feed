# Runtime Execution Architecture Design

## Summary

This spec defines the runtime execution architecture for the content pipeline described in:

- `docs/superpowers/specs/2026-03-26-digest-story-pipeline-design.md`

The target runtime is:

- single-machine first
- `Postgres + Redis + Celery`
- object-level queues for front-stage work
- business-day batch jobs for aggregation stages
- stage re-scan recovery instead of precise in-flight recovery

This spec covers:

- queue and worker boundaries
- stage execution model
- task status and retry semantics
- automatic trigger conditions for day-batch aggregation
- LLM rate limiting
- memory control strategy

This spec does not cover:

- content semantics or schema for `article_event_frame`, `strict_story`, or `digest`
- multi-machine distributed scaling
- exact-once processing guarantees
- near-duplicate content strategy such as SimHash/MinHash
- frontend changes
- chat agent orchestration

## Goals

- Make crawl, parse, normalize, and extraction stages resilient to ordinary failures.
- Avoid one failed object blocking the whole day.
- Use a queue for stages that are naturally object-based.
- Keep event packing and digest generation as aggregation jobs rather than forcing them into object queues.
- Keep business truth in Postgres.
- Use Redis for runtime coordination and queue transport, not truth storage.
- Prevent LLM endpoint contention across multiple workers.
- Keep memory bounded in front stages by persisting results immediately.

## Non-Goals

- No distributed cluster design.
- No requirement to recover every in-flight task after crash.
- No custom Redis queue protocol.
- No visual image-analysis stage in the primary runtime.

## Runtime Principles

### Principle 1: Business truth stays in Postgres

Object state is authoritative in Postgres.

Queue state is not authoritative.

If Redis or a worker loses an in-flight task, the system should recover by:

- scanning Postgres state
- re-enqueueing eligible objects

### Principle 2: Queue only where the task unit is naturally local

Object-level tasks belong in queues:

- collect one source
- parse one article
- normalize one article
- extract frames for one article

Aggregation tasks do not belong in object-level queues:

- pack strict stories for one business day
- generate digests for one business day

Those remain batch jobs because they need a candidate set, not an isolated object.

### Principle 3: Retry must be bounded

For any object in any stage:

- maximum attempts: `3`
- after the third failed attempt: terminal failure
- terminal failure must not block the rest of the pipeline

### Principle 4: Memory must be bounded by stage type

Front stages must process and persist incrementally.

Back aggregation stages may load the current business-day candidate set into memory.

## Components

### `DailyRunCoordinator`

One coordinator process is responsible for:

- determining the active business day
- creating or resuming the current `pipeline_run`
- enqueueing front-stage work
- periodically re-scanning stage backlogs
- deciding when upstream work is sufficiently drained
- enqueueing day-batch aggregation jobs

The coordinator is a control-plane component.
It should not directly perform large object-level workloads.

### `Celery Object Workers`

Celery workers consume front-stage tasks from Redis.

These workers handle object-level operations only.

Primary task types:

- `collect_source(source_name, run_id)`
- `parse_article(article_id)`
- `normalize_article(article_id)`
- `extract_event_frames(article_id)`

### `Day-Batch Aggregation Jobs`

Aggregation jobs may run through Celery as tasks, but their unit is the business day.

Primary batch jobs:

- `pack_strict_stories_for_day(business_day, run_id)`
- `generate_digests_for_day(business_day, run_id)`

They must not be decomposed into object-level queue items because the required semantics are set-based.

## Queue and Storage Roles

### Postgres

Postgres stores:

- business objects
- stage status
- attempts
- failure reason
- timestamps
- `pipeline_run`

Postgres is the only truth source for whether an object has actually completed a stage.

### Redis

Redis stores runtime coordination state only:

- Celery broker payloads
- shared LLM rate-limit tokens or leases
- lightweight locks where required

Redis must not become the canonical record of pipeline progress.

### Celery

Celery provides:

- queue transport
- worker pool management
- retries at the task-execution layer where useful
- operational worker lifecycle management

Application-level retry truth still belongs to Postgres object state.
Celery retry behavior must not create a second independent business retry system.
The authoritative retry count for business work is the stage attempts field stored in Postgres.

The runtime should not depend on Celery result storage as the source of truth.

## Stage Execution Model

### Stage 1: Source Collection

Task unit:

- one source per task

Flow:

1. coordinator enqueues one collection task per eligible source
2. worker fetches that source with bounded internal HTTP concurrency
3. discovered article seeds are persisted immediately
4. new article rows become eligible for parse enqueue

The system should use Celery worker concurrency across sources rather than introducing a separate top-level thread/process orchestration layer.

Inside one source task, bounded async I/O is acceptable.
Unbounded in-memory accumulation is not.

### Stage 2: Article Parse

Task unit:

- one `article_id` per task

Worker responsibilities:

- fetch full page if needed
- parse body blocks and image metadata
- write markdown/body storage
- persist `article_image` rows
- update parse status and attempts

Results must be persisted before the task is considered complete.

### Stage 3: Article Normalize

Task unit:

- one `article_id` per task

Worker responsibilities:

- produce durable normalized Chinese materials required by downstream extraction and digest writing
- persist normalized outputs or a deterministic storage reference
- update normalization state

This stage does not decide final publication.

### Stage 4: Event Frame Extraction

Task unit:

- one `article_id` per task

Worker responsibilities:

- read normalized article material
- extract `0..3` high-confidence event frames
- persist `article_event_frame`
- update extraction state and attempts

This stage is sparse by design.
An article producing zero frames is a valid outcome.

### No Primary `image analysis` Stage

The primary runtime does not include a visual-analysis worker or queue.

Rationale:

- in fashion sources, caption/title/context text already carries strong semantics
- the system should avoid adding a visual LLM dependency to the core runtime

`article_image` remains in the truth model.
Image retrieval may still exist later, but it should be built from source-provided text fields rather than a dedicated visual-analysis service.

## Stage State Model

Each object at each stage should have at least:

- `status`
- `attempts`
- `error`
- `updated_at`

Recommended status values:

- `pending`
- `queued`
- `running`
- `done`
- `failed`
- `abandoned`

Meaning:

- `pending`: eligible but not yet queued
- `queued`: enqueued for worker execution
- `running`: currently claimed by a worker
- `done`: completed successfully
- `failed`: failed but still retryable
- `abandoned`: terminal failure after max attempts

## Retry and Failure Rules

### Per-Object Retry Cap

For every object-stage pair:

- maximum attempts is `3`
- after the third failed attempt, mark the object `abandoned`
- do not enqueue it again for that stage

Rate-limit waiting by itself must not consume one of the three attempts.
An attempt should count only when the worker actually starts the business operation for that object-stage and that operation fails.

### Failure Scope

Failures are local by default.

Examples:

- one source collect failure does not stop other sources
- one article parse failure does not stop other articles
- one normalization failure does not stop other articles
- one frame extraction failure does not stop other articles

### Batch Failure Scope

Day-batch aggregation jobs may fail independently.

If a business-day aggregation job fails:

- mark that batch stage as failed for the run
- allow the coordinator to retry the batch job explicitly
- do not roll back already completed front-stage object work

## Re-Scan Recovery

The system recovers from worker crashes and message loss by re-scanning Postgres state.

Coordinator responsibilities:

- find objects in `pending`
- find retryable objects in `failed` with `attempts < 3`
- detect stale `queued` or `running` objects past a timeout threshold
- convert stale work back into retryable failure state
- re-enqueue eligible objects

This avoids the need for exact in-flight restoration semantics.

### Stale Task Reclamation

Because Redis queue state is not authoritative, `queued` or `running` objects may become stranded after process failure.

The coordinator must periodically reclaim stale objects by:

- checking `updated_at` against a stage-specific timeout
- converting stale `queued` or `running` rows into `failed`
- incrementing attempts as appropriate
- re-enqueueing only if attempts remain below `3`

## Automatic Trigger for Aggregation

Aggregation is triggered automatically after upstream front stages are sufficiently drained for a business day.

### Trigger Condition

For a given business day, front-stage work is considered sufficiently drained when:

- source collection for the run has finished
- no retryable objects remain in front stages
- any remaining unfinished objects are terminal failures only
- the candidate set for the day is stable enough to aggregate

This does not require perfection.
It requires that upstream work has stopped actively producing new retryable objects for the day.

### Aggregation Sequence

Once the trigger condition is met:

1. enqueue `pack_strict_stories_for_day`
2. wait for it to complete successfully
3. enqueue `generate_digests_for_day`

The second job must not start until the first has produced a stable strict-story set for that business day.
The coordinator must prevent duplicate active batch jobs for the same business day and stage.

## LLM Rate Limiting

If multiple workers share the same LLM endpoint, rate limiting must be coordinated globally.

### Coordination Mechanism

Use Redis-backed shared tokens or leases.

Before a worker calls an LLM endpoint, it must acquire a token for the relevant endpoint or model class.

If no token is available:

- wait briefly and retry acquisition
- or fail the task attempt in a controlled way so it can be retried

### Why This Is Required

Per-worker sleep logic is insufficient because:

- workers do not know global concurrency
- one worker can overrun the endpoint while others are already backing off
- endpoint saturation becomes unstable as worker count grows even on one machine

Redis coordination gives one shared throttle point.

## Memory Control Strategy

### Front Stages

Front stages must be streaming and persistence-first.

Rules:

- do not accumulate large article bodies in memory across many objects
- persist results as soon as one object is complete
- keep task-local working sets small and disposable

### Back Aggregation Stages

For now, aggregation may load the current business-day candidate set into memory.

This is acceptable because:

- the target deployment is single-machine first
- front-stage persistence already constrains memory growth elsewhere
- aggregation semantics are naturally set-based

Future sharding is allowed later, but it is not required in this design.

## Observability

The runtime must expose enough information to answer:

- which sources are still collecting
- how many objects are pending, queued, running, failed, or abandoned in each stage
- which objects hit the retry cap
- whether a business day has met the aggregation trigger condition
- which batch job failed and why

`pipeline_run` should remain the run-level anchor, but object tables must carry enough stage-local state for re-scan recovery and operational debugging.

## Operational Boundaries

This runtime design intentionally does not require:

- horizontal worker clusters
- DAG-style orchestration for every stage
- exact-once message delivery
- custom queue semantics beyond Celery and application state

The shortest-path implementation is:

- Celery for queue transport and worker management
- Redis as broker and rate-limit coordinator
- Postgres as business truth
- coordinator-driven re-scan and batch triggering

## Scope Boundaries for Planning

The implementation plan created from this spec should cover:

- coordinator responsibilities
- Celery task topology for front stages
- object-stage state fields and retry rules
- stale task reclamation
- aggregation trigger logic
- Redis-based LLM rate limiting

The implementation plan should not cover:

- SimHash/MinHash
- distributed deployment
- custom queue protocol
- visual image analysis
- chat agent orchestration
- frontend work
