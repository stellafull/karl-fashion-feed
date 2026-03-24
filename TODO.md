# Pipeline Optimization TODO

## Context

The karl-fashion-feed backend implements a content pipeline: article collection → parsing → enrichment → story aggregation → RAG embedding. Current issues:

**Problems:**
1. Image analysis and RAG ingest are completely missing from the main pipeline
2. Failed articles retry indefinitely (no parse_attempts upper limit)
3. Enrichment is serial (for loop with await) - major bottleneck
4. Cluster review has unbounded concurrency (risk of API rate limits)
5. Dense embeddings make N API calls instead of batching
6. Clustering threshold is hardcoded

**Goal:** Complete the pipeline, optimize to ~7x faster, prevent infinite retries, maintain fail-fast semantics.

---

## Implementation Plan

### Priority 1: Integrate image_analysis and rag_ingest into pipeline

**File:** `backend/app/service/scheduler_service.py`

**Workflow order:**
```
collection → parse → enrichment → story_embedding → semantic_cluster →
cluster_review → story_generation → image_analysis → rag_ingest →
story_persist → mark_success
```

**Changes:**
- Add STAGE_IMAGE_ANALYSIS, STAGE_RAG_INGEST, IMAGE_ANALYSIS_CONCURRENCY = 5 constants
- Update WORKFLOW_STAGES tuple to include new stages
- Add ImageAnalysisService and ArticleRagService imports and initialization
- Add `analyze_article_images(publishable_article_ids)` method with Semaphore(5) concurrency
  - Each task gets independent SessionLocal()
  - Only processes publishable articles' images
- Add `ingest_articles_to_rag(article_ids)` method
  - Calls ArticleRagService.upsert_articles() (filters publishable internally)
- Update `run_story_workflow()` to call both stages BEFORE returning
- Update metadata to include image_analysis and rag_ingest statistics

---

### Priority 2: Add MAX_PARSE_ATTEMPTS limit

**File:** `backend/app/service/article_parse_service.py`

**Changes:**
- Add MAX_PARSE_ATTEMPTS = 3 constant
- Update `_load_candidates()` query to filter `parse_attempts < MAX_PARSE_ATTEMPTS`
- Update `_persist_outcomes()` to:
  - Increment parse_attempts on failure
  - Set parse_status = "abandoned" if attempts >= MAX_PARSE_ATTEMPTS
  - Otherwise set parse_status = "failed"

---

### Priority 3: Make enrichment concurrent

**File:** `backend/app/service/scheduler_service.py`

**Changes:**
- Add ENRICHMENT_CONCURRENCY = 10 constant
- Replace `enrich_articles()` method with concurrent version:
  - Load candidates in main session
  - Filter already-enriched articles
  - Process with Semaphore(10)
  - Each task gets independent SessionLocal()
  - Each task loads article, calls enrich_article(), commits
  - Fail-fast: exceptions propagate (no return_exceptions=True)

---

### Priority 4: Add semaphore to cluster review

**File:** `backend/app/service/article_cluster_service.py`

**Changes:**
- Add CLUSTER_REVIEW_CONCURRENCY = 5 constant
- Wrap cluster review API calls with Semaphore(5)
- No truncation of large clusters - preserve full context

---

### Priority 5: Batch dense embeddings

**File:** `backend/app/service/RAG/embedding_service.py`

**Changes:**
- Replace `generate_dense_embedding()` with batched version
- Use DENSE_EMBEDDING_CONFIG.batch_size (default 25)
- Validate texts and image_urls lengths match
- Batch API calls to reduce from N calls to ceil(N/batch_size)

---

### Priority 6: Make cluster threshold configurable

**Files:**
- `backend/app/service/article_cluster_service.py`
- `backend/app/service/scheduler_service.py`

**Changes:**
- ArticleClusterService.__init__ accepts optional distance_threshold parameter (default 0.18)
- Use self._distance_threshold in clustering
- SchedulerService.__init__ accepts optional cluster_distance_threshold parameter
- Pass threshold to ArticleClusterService

---

## Verification Tests

1. **Parse retry limit**: Article with parse_attempts=3 has status="abandoned" and is NOT selected
2. **Workflow ordering**: Image_analysis failure prevents story persist and watermark advance
3. **RAG idempotency**: Same articles ingested twice produce same Qdrant unit counts
4. **Embedding batching**: 26 records with batch_size=25 makes exactly 2 API calls
5. **Threshold config**: Lower threshold → more clusters, higher threshold → fewer clusters
6. **Enrichment concurrency**: No session conflicts, fail-fast preserved
7. **Image analysis scope**: Only publishable articles' images analyzed

---

## Key Assumptions

- Image analysis only for publishable articles in main pipeline
- Keep Qdrant wait=True for fail-fast (change after RAG decoupled)
- No queues, FSM, checkpoints, DLQ, or cross-run story merging
- Stories from previous runs never updated (design invariant)
- Each concurrent task gets independent SessionLocal()
- Parse "abandoned" is terminal - never retried
- Large clusters NOT truncated for review

---

## Expected Performance

**Before:** ~130s (20 articles)
- Enrichment: 60s (serial)
- Cluster review: 20s (unbounded)
- Dense embedding: 50s (1-by-1)

**After:** ~18s (20 articles)
- Enrichment: 6s (concurrent)
- Cluster review: 4s (rate-limited)
- Dense embedding: 2s (batched)
- Image analysis: 4s (concurrent)
- RAG ingest: 2s

**Speedup: 7.2x**

---

## Critical Files

- `backend/app/service/scheduler_service.py` - Main orchestration
- `backend/app/service/article_parse_service.py` - Parse retry limit
- `backend/app/service/article_cluster_service.py` - Cluster review rate limiting
- `backend/app/service/RAG/embedding_service.py` - Batched embeddings
- `backend/app/service/article_enrichment_service.py` - No changes (already session-safe)
