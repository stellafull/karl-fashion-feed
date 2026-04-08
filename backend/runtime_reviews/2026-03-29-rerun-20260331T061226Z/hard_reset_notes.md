# 2026-03-29 Runtime Hard Reset

- created_at_utc: 2026-03-31T06:14:17.883868+00:00
- business_day: 2026-03-29
- action: dropped all non-article runtime/derived tables in dev database
- action: recreated current runtime schema from code
- reset_article_count: 698
- remaining_tables: ['article', 'article_event_frame', 'article_image', 'digest', 'digest_article', 'digest_story', 'pipeline_run', 'source_run_state', 'story', 'story_article', 'story_facet', 'story_frame']
