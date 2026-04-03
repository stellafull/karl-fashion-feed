# Digest Packaging Contract Simplification Design

## Goal

Reduce `digest_packaging` output size so the story-to-digest pipeline can run reliably on large business days without changing the core business chain:

`story -> facet assignment -> digest packaging -> digest report writing`

The target is to preserve editorial direction while removing redundant LLM output fields that can be derived locally.

## Current Problem

`digest_packaging` currently asks the model to output:

- `facet`
- `story_keys`
- `article_ids`
- `editorial_angle`
- `title_zh`
- `dek_zh`

This creates two issues:

1. Output bloat
   `article_ids`, `title_zh`, and `dek_zh` consume output tokens even though they are not the final public artifact.

2. Responsibility duplication
   `digest_report_writing` already generates final `title_zh`, `dek_zh`, `body_markdown`, and `source_article_ids`.
   That means packaging and writing are both doing editorial naming work, and packaging is also doing article selection that the database can derive exactly.

The verified runtime behavior on `2026-03-29` shows that the packaging step can hit very large prompt/output budgets once one facet contains many stories. The bottleneck is not context-window capacity alone; the bigger issue is unnecessary output shape.

## Chosen Design

`digest_packaging` will output only:

- `story_keys`
- `editorial_angle`

Everything else is handled outside the LLM:

- `facet`
  supplied by the current facet-local packaging call
- `article_ids`
  derived locally by unioning `story -> story_article.article_id`
- `source_names`
  derived locally from selected articles
- `title_zh`
  generated only in `digest_report_writing`
- `dek_zh`
  generated only in `digest_report_writing`

This keeps the only LLM-only packaging responsibility as:

- deciding which stories should be grouped into one digest
- expressing the editorial angle that should constrain downstream writing

## Why Keep `editorial_angle`

Dropping `editorial_angle` entirely would minimize tokens further, but it weakens downstream control.

`digest_report_writing` receives raw article content. Without an explicit angle, the writer model is more likely to:

- over-index on whichever source article is longest or most vivid
- flatten a multi-story digest into a generic summary
- drift away from the intended Chinese editorial framing

Keeping `editorial_angle` provides a lightweight semantic constraint:

- `story_keys` define the hard factual boundary
- `editorial_angle` defines the soft narrative direction
- source articles provide factual density for final writing

This is the shortest path that still preserves editorial coherence.

## New Contracts

### `digest_packaging`

New schema:

```json
{
  "digests": [
    {
      "story_keys": ["..."],
      "editorial_angle": "..."
    }
  ]
}
```

Rules:

- packaging runs one facet at a time
- returned `story_keys` must come from the input facet-local story set
- `editorial_angle` must be non-empty Chinese editorial guidance
- packaging no longer returns `facet`, `article_ids`, `title_zh`, or `dek_zh`

### Local packaging resolution

For each packaging result:

1. `facet = current facet argument`
2. `article_ids = union(story_keys -> story_article.article_id)` preserving stable order
3. `source_names = derived from article rows`

This local resolution must be deterministic and fail fast if any referenced story is missing.

### `digest_report_writing`

Writer input becomes:

- `facet`
- `story_keys`
- `editorial_angle`
- compact story synopsis list
- resolved `article_ids`
- source article raw title / summary / body markdown

Writer output stays:

- `title_zh`
- `dek_zh`
- `body_markdown`
- `source_article_ids`

`source_article_ids` is still useful here because writer may legitimately choose a subset of resolved source articles for the final public digest.

## Data Flow

1. `StoryFacetAssignmentService`
   assigns one or more runtime facets to each story.

2. `DigestPackagingService`
   runs per facet and returns only digest groupings plus editorial angles.

3. Local resolver
   derives `article_ids` and `source_names` from `story_keys`.

4. `DigestReportWritingService`
   writes final Chinese title, dek, and body using:
   facet + editorial angle + stories + resolved article content.

5. `DigestGenerationService`
   persists digest rows and memberships as before.

## Fail-Fast Rules

- Packaging result with blank `story_keys` or blank `editorial_angle` is invalid.
- Packaging result containing unknown or duplicate `story_keys` is invalid.
- Local article resolution that yields zero articles for a selected story set is invalid.
- Writer result with unknown or duplicate `source_article_ids` is invalid.
- If packaging has valid input stories but produces zero digest plans, the run fails.

## Expected Benefits

- Lower packaging output token volume
- Clearer stage ownership
- Less duplicated editorial generation
- Better runtime stability on large business days
- Easier debugging because each stage has one narrow responsibility

## Out of Scope

- Changing facet taxonomy
- Changing final digest writing style
- Replacing facet assignment with a non-LLM rule system
- Redesigning public digest persistence schema

## Implementation Notes

Minimal code changes should be limited to:

- `backend/app/schemas/llm/digest_packaging.py`
- `backend/app/prompts/digest_packaging_prompt.py`
- `backend/app/service/digest_packaging_service.py`
- `backend/app/service/digest_generation_service.py`
- `backend/app/service/digest_report_writing_service.py`
- affected tests

No compatibility shim is needed. This is a direct contract replacement inside the current runtime path.
