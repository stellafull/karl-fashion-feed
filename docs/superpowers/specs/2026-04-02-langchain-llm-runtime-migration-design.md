# LangChain LLM Runtime Migration Design

## Summary

This spec migrates the backend runtime from direct OpenAI SDK calls to a single LangChain-based execution style.

The migration target is:

- `openai-compatible` provider only
- `create_agent` as the default runtime primitive
- `open_deep_research`-style configuration and retry strategy
- no compatibility shims
- no dual-path runtime
- no `message/session` product design in this phase

This spec covers:

- target runtime contract for LLM calls
- configuration shape
- service-by-service migration scope
- RAG agent and tool boundaries
- retry, failure, and observability semantics
- test strategy

This spec does not cover:

- product-level `message/session` schema
- persistent chat history design
- LangGraph checkpointer or memory rollout
- multi-provider compatibility beyond `openai-compatible`
- future multi-agent orchestration above the current backend runtime

## Goals

- Remove direct `openai.AsyncOpenAI` usage from backend runtime services.
- Replace scattered `chat.completions.create` calls with one LangChain execution style.
- Keep the migration as the shortest path that satisfies current runtime needs.
- Use `create_agent` for both structured output and tool-calling paths.
- Reuse LangChain retry behavior with `3` attempts for transient model and network failures.
- Preserve fail-fast business validation in service code.
- Keep the RAG capability callable both as an internal service and as a tool for future agents.

## Non-Goals

- No fallback from structured output to free-form text.
- No compatibility wrapper for old OpenAI response payload shapes.
- No product conversation storage design.
- No persistent agent thread state for the current migration.
- No LangGraph workflow refactor for pipeline stages.
- No generic helper pyramid above LangChain.

## Confirmed Decisions

### Decision 1: Provider scope

The runtime supports `openai-compatible` providers only.

The configuration surface may vary by:

- `model`
- `api_key`
- `base_url`
- `timeout`
- `max_tokens`
- `temperature`

The runtime will not implement a provider compatibility matrix.
If a selected provider or model does not support the required structured output or tool-calling behavior, the runtime must fail fast.

### Decision 2: LangChain style

The coding style should follow the practical shape used by `langchain-ai/open_deep_research`:

- one `Configuration` model
- values loaded from environment and runnable config
- thin model initialization
- direct use of `create_agent`
- retry attached at the model boundary

The project must not add a large custom abstraction layer on top of LangChain.

### Decision 3: Runtime primitive

`create_agent` is the default primitive for all current backend LLM execution.

This includes:

- structured output stages
- tool-calling RAG stages
- plain text generation stages

The system should not mix:

- direct OpenAI SDK calls
- one-off manual response parsing
- hand-written model tool loops

### Decision 4: Retry strategy

The runtime should reuse LangChain-style retries with `3` attempts.

The retry target is transient execution failure only, such as:

- network instability
- upstream timeout
- transient model-side failure
- transient structured output failure handled by the LangChain boundary

Business validation errors are not retriable.

### Decision 5: State scope

This migration does not design product-level `message/session` persistence.

For the current migration:

- pipeline stages are stateless single invocations
- `RagAnswerService` remains callable without persistent thread state
- no LangGraph `checkpointer` is introduced into the runtime path

If future top-level chat agents need resumable thread state, that is a separate design.

## Current Problems

The current backend runtime has several concrete boundary problems.

### Problem 1: Direct SDK usage is duplicated

Multiple services construct `AsyncOpenAI` clients and call `chat.completions.create` directly.

This duplicates:

- provider configuration
- timeout behavior
- retry policy
- request shape
- raw response handling

### Problem 2: Structured output handling is brittle

The current services rely on:

- prompt instructions for JSON discipline
- manual extraction from `message.content`
- local `model_validate_json(...)`

This has already failed in practice with:

- unsupported business values such as invalid facets
- fenced JSON output
- long blocking calls that were hard to diagnose because the boundary was scattered

### Problem 3: RAG uses a handwritten tool loop

`RagAnswerService` currently implements its own model loop, message serialization, and tool-call dispatch.

That creates a second runtime style in the same codebase and makes future agent composition harder.

### Problem 4: Boundary concerns are mixed with business logic

Service code currently combines:

- prompt preparation
- model transport
- response extraction
- schema parsing
- business validation
- persistence

The migration should narrow each service back to:

- business input preparation
- business validation
- persistence
- debug capture

## Target Architecture

The target runtime contract is:

`Configuration -> LangChain chat model -> create_agent -> service-level business validation`

Only the LangChain path may execute LLM work in backend runtime services.

### Layer 1: Configuration

The runtime will expose a single `Configuration` model in the LLM config module.

It should follow the `open_deep_research` pattern:

- Pydantic model
- values loaded from env and optional runnable config
- one place to resolve runtime defaults

The field name for provider endpoint must be:

- `base_url`

Not:

- `openai_compatible_base_url`

The configuration only needs fields required by this migration.

Minimum expected fields:

- `base_url`
- `api_key`
- `max_structured_output_retries`
- `story_summarization_model`
- `story_summarization_model_max_tokens`
- `story_summarization_temperature`
- `rag_model`
- `rag_model_max_tokens`
- `rag_temperature`
- `max_react_tool_calls`
- optional debug flags as needed by current runtime

### Layer 2: Model initialization

The runtime should keep one thin model initialization entry point.

Its responsibilities are:

- initialize the LangChain chat model for an `openai-compatible` endpoint
- apply common timeout and retry settings
- provide the model to services in a small number of profiles

Its responsibilities do not include:

- business validation
- generic parsing helpers
- compatibility fallback behavior
- product session management

This layer may use `langchain-openai`.

### Layer 3: Service-local agents

Each service should construct its own `create_agent` usage close to its prompt and schema.

This keeps the code short and avoids a generic agent registry.

The service-local agent should define:

- model profile
- system prompt
- response schema or tools
- any service-specific runtime options

### Layer 4: Business validation and persistence

After LangChain returns structured data or tool results, existing services must continue to perform business validation.

Examples:

- reject unsupported facets
- reject unknown story keys or article ids
- reject blank required fields
- reject invalid digest source memberships

These checks remain explicit and fail fast.

## Service Migration Scope

### `EventFrameExtractionService`

Target file:

- `backend/app/service/event_frame_extraction_service.py`

Migration:

- remove direct `AsyncOpenAI` usage
- replace direct model call with `create_agent(..., response_format=EventFrameExtractionSchema)`
- keep current markdown loading, article checks, and frame persistence

The service remains a single-article stateless invocation.

### `StoryClusteringService`

Target file:

- `backend/app/service/story_clustering_service.py`

Migration:

- remove direct `AsyncOpenAI` usage
- replace window judgment model call with `create_agent(..., response_format=StoryClusterJudgmentSchema)`
- keep candidate window generation, normalization, singleton completion, and full coverage assertions

The service must continue to fail fast on invalid clustering output at the business layer.

### `StoryFacetAssignmentService`

Target file:

- `backend/app/service/story_facet_assignment_service.py`

Migration:

- remove direct `AsyncOpenAI` usage
- replace manual JSON parsing with `create_agent(..., response_format=FacetAssignmentSchema)`
- keep batching logic
- keep explicit runtime facet validation against `RUNTIME_FACETS`

Invalid facets remain a hard error.

### `DigestPackagingService`

Target file:

- `backend/app/service/digest_packaging_service.py`

Migration:

- remove direct `AsyncOpenAI` usage
- replace manual JSON parsing with `create_agent(..., response_format=DigestPackagingSchema)`
- keep grouping by facet
- keep story and article membership validation

The service must not add any fallback packaging behavior.

### `DigestReportWritingService`

Target file:

- `backend/app/service/digest_report_writing_service.py`

Migration:

- remove direct `AsyncOpenAI` usage
- replace manual JSON parsing with `create_agent(..., response_format=DigestReportWritingSchema)`
- keep markdown loading
- keep source article validation
- keep final digest object resolution

### `RagAnswerService`

Target file:

- `backend/app/service/RAG/rag_answer_service.py`

Migration:

- remove the handwritten tool loop
- build the RAG runtime with `create_agent(..., tools=[...])`
- keep business-layer result normalization and citation construction
- preserve streaming behavior for answer synthesis

`RagAnswerService` must remain usable in two ways:

- as the current direct answer service
- as a tool exposed to future higher-level agents

That means the service boundary stays, even though its internal execution becomes agent-based.

## RAG Design

### Tool boundary

The existing retrieval and web-search capabilities should remain business tools, not be reimplemented inside the agent layer.

The LangChain agent may call tools that wrap the current RAG domain operations, including:

- article and image retrieval
- optional web search
- result packaging for answer synthesis

The tool implementations should reuse current domain services where possible.

### Agent shape

The RAG path should use the simplest agent shape that satisfies current needs:

- one `create_agent`
- a bounded tool budget from configuration
- no checkpointer
- no durable thread state

The runtime should not introduce a larger graph unless a later design requires it.

### Tool exposure to future agents

The project should provide a clear adapter that exposes RAG answering as a tool.

That adapter should call `RagAnswerService` rather than duplicating its retrieval logic.

This keeps one canonical retrieval-and-answer path in the codebase.

## Failure Semantics

### Principle 1: Transport and execution retries belong to LangChain

The runtime should rely on LangChain retry behavior with `3` attempts.

Service code should not implement its own generic execution retry loops.

### Principle 2: Business validation errors are terminal

These failures must surface immediately:

- unsupported facet values
- unknown story or article references
- blank required fields
- invalid output relationships

These are correctness failures, not transient transport failures.

### Principle 3: No fallback content mode

If structured output fails, the runtime must not:

- downgrade to free-form text
- strip fences and try ad hoc parsing
- attempt alternate prompts in hidden helper code

The runtime should crash early and expose the failure.

## Observability

Current debug-artifact capture remains useful and should stay.

The recorded artifact format may change, but the debugging goal stays the same:

- inspect model input
- inspect model configuration summary
- inspect structured result or raised exception

The migration must not depend on raw OpenAI SDK response objects.

Artifact capture should remain attached to existing business stages rather than moving into a generic global logger.

## Dependency Changes

The runtime will require the LangChain provider package for `openai-compatible` chat models.

Expected dependency change:

- add `langchain-openai`

The project should continue to manage Python dependencies with `uv`.

## Testing Strategy

### Service tests remain primary

The migration should preserve existing service-level tests as much as possible.

The main change is the test seam:

- old seam: fake OpenAI SDK client
- new seam: fake LangChain model or fake agent return value

### Required coverage

The migration should cover:

- structured output success for each service
- fail-fast business validation on invalid output
- retry-aware transient failure behavior
- RAG tool invocation and citation preservation
- streaming behavior for RAG output

### Acceptance verification

At minimum, the migration must pass the current runtime-focused backend test suite covering:

- story clustering
- facet assignment
- digest packaging
- digest generation
- story-digest runtime integration

Additional RAG migration tests should be added where current coverage is insufficient.

## Out of Scope for This Migration

The following items are explicitly deferred:

- product `message/session` tables
- user-visible conversation history storage
- persistent thread state with LangGraph `checkpointer`
- long-term memory or `store`
- cross-agent orchestration above the current RAG boundary

These may be designed later, but they must not expand the current migration scope.

## Acceptance Criteria

This migration is complete when all of the following are true:

- backend runtime services no longer import or construct `AsyncOpenAI`
- backend runtime services no longer call `chat.completions.create`
- structured output stages run through LangChain `create_agent`
- RAG tool calling runs through LangChain `create_agent`
- `RagAnswerService` remains callable as a service and exposable as a tool
- runtime config uses `Configuration` style with `base_url`
- retry behavior is standardized at `3` attempts through the LangChain boundary
- no compatibility shims or dual execution paths remain
- existing business validation remains explicit and fail-fast

## Final Scope Statement

This is a boundary migration, not a product chat-memory project.

The shortest correct path is:

- move all current runtime LLM execution to LangChain
- keep service business logic explicit
- keep failure visible
- defer `message/session` and persistent agent state to a later spec
