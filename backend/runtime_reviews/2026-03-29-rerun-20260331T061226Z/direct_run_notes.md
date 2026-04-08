# 2026-03-29 Direct Service Run

- Switched to direct service orchestration after coordinator+worker queue starvation between parse and event_frame.
- Dev-only override: patched LlmRateLimiter.lease to a no-op during event_frame extraction to avoid Redis single-lease serialization.
- Business logic, prompts, models, clustering, and digest generation still use the current service implementations.
- review_bundle: backend/runtime_reviews/2026-03-29-rerun-20260331T061226Z
- run_id: 3ddbe4f0-32fe-45db-8a1c-9160351d8968
