# RAG Answer API with Brave Search

## Context

**Completed Infrastructure (tasks2.md):**
- ✅ Retrieval core: QueryService executes QueryPlan → QueryResult
- ✅ 4 retrieval modes: text_only, image_only (text/image), fusion
- ✅ Hybrid search (dense + sparse) with reranking
- ✅ PostgreSQL grounding for citations
- ✅ RagTools: deterministic tool dispatch

**New Requirements:**
Build a single answer-oriented endpoint (not debugging/retrieval endpoint). LLM orchestrates up to 3 tool calls (3 RAG tools + 1 Brave web search) and returns Chinese answer with citations.

**Key Design Principles:**
- Single endpoint: `POST /api/v1/rag/query`
- Request: multipart/form-data only
- Image: request-scoped context (not in LLM tool params)
- 4 LLM tools: search_fashion_articles, search_fashion_images, search_fashion_fusion, search_web
- Max 3 tool calls total
- Brave only (no Tavily fallback, fail fast)
- Response: answer + RAG citations [C1] + web citations [W1] + RAG packages + web results
- Filters/limit: request-level constraints (LLM cannot modify)

---

## API Design

### Single Endpoint

```
POST /api/v1/rag/query
```

**Request Format:** `multipart/form-data`

**Request Fields:**
- `query`: str | null - Text query
- `image`: UploadFile | null - Image file upload
- `source_names`: list[str] - Filter by sources (RAG only)
- `categories`: list[str] - Filter by categories (RAG only)
- `brands`: list[str] - Filter by brands (RAG only)
- `tags`: list[str] - Filter by tags (RAG only)
- `start_at`: str | null - Time range start, ISO 8601 (RAG only)
- `end_at`: str | null - Time range end, ISO 8601 (RAG only)
- `limit`: int = 10 - Max results per retrieval

**Validation Rules:**
- At least one of `query` or `image` must be provided
- `start_at < end_at` if both provided
- Filters and limit are hard constraints (LLM cannot modify)

**Response Fields:**
- `answer`: str - Chinese Markdown answer
- `citations`: list[AnswerCitation] - RAG [C1] + web [W1] citations
- `packages`: list[ArticlePackage] - RAG evidence only (deduplicated)
- `query_plans`: list[QueryPlan] - Executed RAG plans only
- `web_results`: list[WebSearchResult] - Brave results (if used)

**Citation Rules:**
- RAG citations: [C1], [C2]... → article/article_image/chunk_index
- Web citations: [W1], [W2]... → Brave title/url/snippet
- Packages contain RAG evidence only (not web)
- Web results returned separately

---

## Implementation Approach

### 1. Update QueryPlan Schema
**File:** `/root/karl-fashion-feed/backend/app/schemas/rag_query.py`

**Changes:**
- `QueryPlan.fusion`: Allow `text_query` (required) + `image_query` (optional)
- `image_query` semantic: "request_image" (stable reference) or external URL
- **NOT base64**: QueryPlan is returned to client, cannot contain large objects
- `image_only`: Keep text_query XOR image_query validation

**Rationale:** QueryPlan must be serializable and client-safe

### 2. Create Request Context
**File:** `/root/karl-fashion-feed/backend/app/schemas/rag_api.py`

**New Models:**
- `RagRequestContext`: Request-scoped context
  - `request_image_base64`: str | None
  - `filters`: QueryFilters
  - `limit`: int
- `RagQueryRequest`: HTTP request DTO
- `RagAnswerResponse`: answer, citations, packages, query_plans, web_results
- `AnswerCitation`: type (rag/web), stable reference
- `WebSearchResult`: title, url, snippet

**Rationale:** Image is request context, not LLM tool parameter

### 3. Refactor RagTools for LLM
**File:** `/root/karl-fashion-feed/backend/app/service/RAG/rag_tools.py`

**LLM-Facing Tools (4 total):**
- `search_fashion_articles(query: str)` → text evidence
- `search_fashion_images(text_query: str | None = None, use_request_image: bool = False)` → image evidence
- `search_fashion_fusion(query: str, use_request_image: bool = False)` → text + image
- `search_web(query: str)` → Brave results

**Key Changes:**
- LLM only controls: query text, use_request_image flag
- LLM does NOT see: filters, limit, image_base64
- RagAnswerService injects filters/limit from request context
- `use_request_image=True` + no uploaded image → fail fast
- Image routing:
  - Text-to-image: `text_query` provided, `use_request_image=False`
  - Image-to-image: `text_query=None`, `use_request_image=True`
  - Fusion with image: `query` + `use_request_image=True`
  - Fusion text-only: `query` + `use_request_image=False`

### 4. Update QueryService for Request Image Context
**File:** `/root/karl-fashion-feed/backend/app/service/RAG/query_service.py`

**Changes:**
- `execute()` accepts optional `request_image_base64` parameter
- When `query_plan.image_query == "request_image"`, use request context base64 for embedding
- When `image_query` is URL or empty, maintain existing behavior
- **Do NOT** put request image base64 in QueryPlan or QueryResult

### 5. Update Embedding Service
**File:** `/root/karl-fashion-feed/backend/app/service/RAG/embedding_service.py`

**Changes:**
- Change interface from "texts + image_urls" to "texts + image_inputs"
- Image input supports: URL or base64 string
- Unified entry point (no business layer URL/base64 branching)

### 6. Implement Web Search Service
**Files:**
- `/root/karl-fashion-feed/backend/app/config/search_config.py` (new)
- `/root/karl-fashion-feed/backend/app/service/RAG/web_search_service.py` (new)

**Responsibilities:**
- Call Brave Search API only (no Tavily fallback)
- Parse and return: title, url, snippet
- Fail fast on errors (no silent empty results)

**Configuration:**
- Read `BRAVE_API_KEY` from environment
- Missing key → fail fast
- Use existing `aiohttp` for HTTP client

**No Fallback:**
- Brave fails → propagate error
- No multi-provider abstraction
- No silent empty results

### 7. Implement RagAnswerService
**File:** `/root/karl-fashion-feed/backend/app/service/RAG/rag_answer_service.py` (new)

**Responsibilities:**
- Maintain RagRequestContext (image, filters, limit)
- LLM tool loop using AsyncOpenAI.chat.completions.create()
- Max 3 tool calls (RAG + web combined)
- Accumulate QueryResults and WebSearchResults
- Deduplicate RAG packages by article_id
- Generate stable citations: [C1], [C2]... (RAG), [W1], [W2]... (web)
- Final Chinese synthesis

**Tool Loop Rules:**
- LLM decides: which tool, query text, use_request_image, continue/stop
- LLM cannot modify: filters, limit
- search_web used when: RAG insufficient OR question needs external/latest info
- After 3rd call: force synthesis

**Synthesis Rules:**
- Provide RAG + web evidence to LLM
- Output Chinese Markdown answer
- Citation markers must match citations list
- Weak evidence → state uncertainty in answer, but still return answer

### 8. Create Router
**File:** `/root/karl-fashion-feed/backend/app/router/rag_router.py` (new)

**Responsibilities:**
- Parse multipart/form-data
- Validate query/image presence, time range
- Convert uploaded image to base64 (in-memory only)
- Build RagRequestContext
- Call RagAnswerService
- Return RagAnswerResponse

**Image Handling:**
- Read uploaded file into memory
- Encode to base64 string
- No temp file on disk
- Request-scoped only

### 9. Initialize FastAPI App
**File:** `/root/karl-fashion-feed/backend/app/app_main.py` (new)

**Setup:**
- Create FastAPI app
- Add CORS middleware
- Include rag_router with `/api/v1` prefix

---

## Key Implementation Decisions

1. **Image as request context**: Not in LLM tool params, injected by RagAnswerService
2. **QueryPlan stays clean**: No base64, only "request_image" reference or URL
3. **LLM controls minimal surface**: query text + use_request_image flag only
4. **Filters/limit immutable**: Request-level constraints, LLM cannot modify
5. **Brave only, fail fast**: No Tavily fallback, no silent empty results
6. **Max 3 tool calls**: Hard limit across all tools (RAG + web)
7. **Dual citation system**: [C#] for RAG, [W#] for web
8. **Separate evidence streams**: packages (RAG only), web_results (Brave only)
9. **Always return answer**: No clarification state, state uncertainty in answer
10. **In-memory image**: No temp files, base64 in memory only

---

## Critical Files

### Files to Modify:
1. `/root/karl-fashion-feed/backend/app/schemas/rag_query.py`
   - Update fusion: text_query required, image_query optional
   - image_query: "request_image" or URL (not base64)

2. `/root/karl-fashion-feed/backend/app/service/RAG/rag_tools.py`
   - Refactor to 4 LLM-facing tools
   - Remove filters/limit/image_base64 from tool signatures
   - Add use_request_image flag

3. `/root/karl-fashion-feed/backend/app/service/RAG/query_service.py`
   - Add request_image_base64 parameter to execute()
   - Handle image_query="request_image"

4. `/root/karl-fashion-feed/backend/app/service/RAG/embedding_service.py`
   - Change to unified image input (URL or base64)

### Files to Create:
1. `/root/karl-fashion-feed/backend/app/schemas/rag_api.py`
   - RagRequestContext, RagQueryRequest, RagAnswerResponse
   - AnswerCitation, WebSearchResult

2. `/root/karl-fashion-feed/backend/app/config/search_config.py`
   - Brave API configuration

3. `/root/karl-fashion-feed/backend/app/service/RAG/web_search_service.py`
   - Brave Search API integration

4. `/root/karl-fashion-feed/backend/app/service/RAG/rag_answer_service.py`
   - LLM tool loop, evidence accumulation, synthesis

5. `/root/karl-fashion-feed/backend/app/router/rag_router.py`
   - Single POST endpoint

6. `/root/karl-fashion-feed/backend/app/app_main.py`
   - FastAPI app initialization

---

## Implementation Sequence

1. Update QueryPlan schema (image_query="request_image")
2. Create rag_api.py schemas (RagRequestContext, DTOs)
3. Create search_config.py and web_search_service.py (Brave only)
4. Update embedding_service.py (unified image input)
5. Update query_service.py (request_image_base64 param)
6. Refactor rag_tools.py (4 tools, use_request_image flag)
7. Implement rag_answer_service.py (LLM loop, synthesis)
8. Create rag_router.py (multipart parsing, context building)
9. Create app_main.py (FastAPI app)
10. End-to-end testing

---

## Test Requirements

Must verify:
- ✅ QueryPlan validates image_query="request_image"
- ✅ use_request_image=True + no image → fail fast
- ✅ search_fashion_images: text-to-image vs image-to-image routing
- ✅ search_fashion_fusion: text-only vs text+image routing
- ✅ QueryService uses request_image_base64 when image_query="request_image"
- ✅ LLM tools do NOT expose filters/limit/image_base64
- ✅ RagAnswerService max 3 tool calls
- ✅ Brave API integration works
- ✅ Brave failure → propagate error (no silent fallback)
- ✅ RAG packages deduplicated by article_id
- ✅ Citations: [C#] for RAG, [W#] for web
- ✅ packages contains RAG only, web_results separate
- ✅ query_plans records RAG only (not web)
- ✅ Time range validation (start_at < end_at)

---

## Verification

### Start Server
```bash
cd /root/karl-fashion-feed/backend
uvicorn app.app_main:app --reload --host 0.0.0.0 --port 8000
```

### Test Cases

**1. Text-only query (文搜文):**
```bash
curl -X POST http://localhost:8000/api/v1/rag/query \
  -F "query=春季时尚趋势" \
  -F "limit=10"
```
Expected: Chinese answer with text citations, query_plans shows text_only

**2. Text-to-image query (文搜图):**
```bash
curl -X POST http://localhost:8000/api/v1/rag/query \
  -F "query=红色连衣裙" \
  -F "brands=Dior" \
  -F "brands=Chanel" \
  -F "limit=10"
```
Expected: Answer with image citations, query_plans shows image_only

**3. Image-to-image query (图搜图):**
```bash
curl -X POST http://localhost:8000/api/v1/rag/query \
  -F "image=@/path/to/dress.jpg" \
  -F "limit=10"
```
Expected: Answer with similar image citations

**4. Fusion query (图文融合):**
```bash
curl -X POST http://localhost:8000/api/v1/rag/query \
  -F "query=巴黎时装周" \
  -F "image=@/path/to/runway.jpg" \
  -F "categories=runway" \
  -F "limit=10"
```
Expected: Answer with text + image citations

**5. Multi-round retrieval with web search:**
```bash
curl -X POST http://localhost:8000/api/v1/rag/query \
  -F "query=2024年春季时装周最新趋势和价格" \
  -F "limit=5"
```
Expected: query_plans + web_results, answer combines RAG + web, citations include [C1] and [W1]

**6. Time-filtered RAG query:**
```bash
curl -X POST http://localhost:8000/api/v1/rag/query \
  -F "query=最近的时尚趋势" \
  -F "start_at=2024-01-01T00:00:00Z" \
  -F "end_at=2024-03-23T23:59:59Z" \
  -F "limit=10"
```
Expected: query_plans shows time filters applied

**7. Validation errors:**
```bash
# Missing both query and image
curl -X POST http://localhost:8000/api/v1/rag/query \
  -F "limit=10"
```
Expected: 422 Unprocessable Entity

---

## Dependencies

Existing packages:
- ✅ `fastapi[standard]>=0.135.1`
- ✅ `python-multipart`
- ✅ `pydantic`
- ✅ `aiohttp` (for Brave API)
- ✅ LLM client (from llm_config.py)
- ✅ All RAG dependencies

---

## Environment Variables

Required:
- `BRAVE_API_KEY`: Brave Search API key (fail fast if missing)
- `QDRANT_URL`, `QDRANT_API_KEY`: Existing
- `DASHSCOPE_API_KEY`: Existing (for embeddings)
- `POSTGRES_*`: Existing (for grounding)
- `RAG_CHAT_MODEL_*`: Existing (for LLM loop)








