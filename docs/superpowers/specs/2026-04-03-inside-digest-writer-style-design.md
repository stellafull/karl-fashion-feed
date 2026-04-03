# Inside Digest Writer Style Design

## Goal

Adjust `digest_report_writing` so the generated digest reads like an internal company-facing inside digest instead of a fashion magazine feature.

This is a style-layer change only. It does not change pipeline structure, retrieval scope, or digest persistence shape.

## Audience

The primary readers are internal colleagues at a light-luxury, full-category fashion company.

The digest should be useful for:

- brand and business teams
- merchandising and product teams
- designers who need fast access to current fashion signals

## Core Direction

Use a constrained internal-digest style rather than a magazine or editorial feature style.

Keep the writer flexible in how it organizes a digest, but hard-constrain the voice and priorities.

## Chosen Writing Approach

Do not force a rigid body structure.

Instead, constrain the writer with:

- internal-reader orientation
- concise inside-digest tone
- objective and restrained language
- higher information density around brand actions, product signals, and trend changes
- short default length, with natural expansion when a digest includes multiple stories

This preserves LLM editorial judgment while preventing drift into glossy magazine prose.

## Required Style Rules

### Voice

- Write for internal readers, not public consumers.
- Use an inside-digest tone, not a feature-article tone.
- Keep language factual, direct, and restrained.
- Do not perform lifestyle narration or atmospheric scene-setting unless it directly carries business signal.

### Title

- `title_zh` may remain editorialized.
- It should still stay anchored to concrete facts, brands, categories, or themes.
- Avoid vague or floating “trend piece” titles that could fit any story set.

### Dek

- Keep `dek_zh`.
- Limit it to one concise sentence.
- It should summarize the digest’s main angle in an internal-reader-friendly way.

### Body

- Default target is quick internal reading, roughly the feel of a `250-400` character Chinese short digest.
- This is not a hard cap.
- If one digest contains multiple stories or a denser theme, the body may naturally become longer.
- Let the model decide the exact organization, but prioritize useful information over literary flow.

## Information Priorities

When choosing what to emphasize, bias toward:

- brand actions
- product or category signals
- designer-relevant trend movement
- consumer-facing visual or styling signals when they are concrete

Do not force every digest to include all of the above.
Let the model select the strongest angle from the provided `editorial_angle`, `story_summaries`, and source articles.

## Explicitly Avoid

The writer must suppress magazine-style habits, especially:

- exaggerated rhetoric
- emotional or theatrical openings
- lush aesthetic adjectives without informational value
- empty trend language that sounds stylish but carries little business signal
- overwriting that turns a digest into a long magazine feature

## Relationship To Existing Inputs

The current downstream writer input already contains:

- `facet`
- `editorial_angle`
- `story_summaries`
- resolved source articles

These are sufficient to steer tone and content selection.

No additional hard structure is needed at this stage.

## Non-Goals

- no change to digest ordering rules
- no change to packaging contract
- no requirement to add explicit “what this means for us” takeaways
- no forced subsection headings
- no change to public persistence schema

## Implementation Target

This design should primarily land as prompt changes in:

- `backend/app/prompts/digest_report_writing_prompt.py`

Tests should be updated only as needed to reflect the intended style contract without overfitting to one exact body structure.
