"""Business-day strict-story packing service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.config.llm_config import STORY_SUMMARIZATION_MODEL_CONFIG
from backend.app.models import ArticleEventFrame, Story, StoryArticle, StoryFrame
from backend.app.prompts.strict_story_tiebreak_prompt import build_strict_story_tiebreak_prompt
from backend.app.schemas.llm.strict_story_tiebreak import (
    StrictStoryTieBreakSchema,
    normalize_readable_synopsis_zh,
)
from backend.app.service.llm_rate_limiter import LlmRateLimiter

if TYPE_CHECKING:
    from openai import AsyncOpenAI


@dataclass(frozen=True)
class _CandidateGroup:
    signature_json: dict
    frame_ids: tuple[str, ...]
    article_ids: tuple[str, ...]
    signature_token: str


@dataclass(frozen=True)
class _ExistingStory:
    story_key: str
    signature_json: dict
    frame_ids: tuple[str, ...]
    signature_token: str


@dataclass(frozen=True)
class _ResolvedStory:
    story_key: str
    signature_json: dict
    synopsis_zh: str
    frame_ids: tuple[str, ...]
    article_ids: tuple[str, ...]
    signature_token: str


class StrictStoryPackingService:
    """Pack one business-day event-frame set into immutable strict stories."""

    def __init__(
        self,
        *,
        client: AsyncOpenAI | None = None,
        rate_limiter: LlmRateLimiter | None = None,
    ) -> None:
        self._client = client
        self._rate_limiter = rate_limiter or LlmRateLimiter()

    async def pack_business_day(
        self,
        session: Session,
        business_day: date,
        *,
        run_id: str,
    ) -> list[Story]:
        """Pack one business day and fully replace stored strict_story state for that day."""
        frames = self._load_day_frames(session, business_day)
        candidate_groups = self._group_by_signature(frames)
        existing = self._load_existing_stories(session, business_day)
        resolved = await self._resolve_story_keys(candidate_groups, existing)
        return self._replace_day_rows(session, business_day, run_id=run_id, resolved=resolved)

    def _load_day_frames(self, session: Session, business_day: date) -> list[ArticleEventFrame]:
        statement = (
            select(ArticleEventFrame)
            .where(ArticleEventFrame.business_date == business_day)
            .order_by(ArticleEventFrame.event_frame_id.asc())
        )
        return list(session.scalars(statement).all())

    def _group_by_signature(self, frames: list[ArticleEventFrame]) -> list[_CandidateGroup]:
        grouped: dict[str, dict[str, object]] = {}
        for frame in frames:
            signature_payload = {
                "event_type": frame.event_type,
                "signature_json": frame.signature_json or {},
            }
            token = self._signature_token(signature_payload)
            slot = grouped.setdefault(
                token,
                {
                    "signature_json": signature_payload,
                    "frame_ids": [],
                    "article_ids": set(),
                },
            )
            frame_ids = slot["frame_ids"]
            article_ids = slot["article_ids"]
            assert isinstance(frame_ids, list)
            assert isinstance(article_ids, set)
            frame_ids.append(frame.event_frame_id)
            article_ids.add(frame.article_id)

        groups = [
            _CandidateGroup(
                signature_json=dict(payload["signature_json"]),
                frame_ids=tuple(sorted(payload["frame_ids"])),
                article_ids=tuple(sorted(payload["article_ids"])),
                signature_token=token,
            )
            for token, payload in grouped.items()
        ]
        return sorted(groups, key=lambda item: item.signature_token)

    def _load_existing_stories(self, session: Session, business_day: date) -> list[_ExistingStory]:
        stories = list(
            session.scalars(
                select(Story)
                .where(Story.business_date == business_day)
                .order_by(Story.story_key.asc())
            ).all()
        )
        if not stories:
            return []

        frame_memberships = session.execute(
            select(StoryFrame.story_key, StoryFrame.event_frame_id)
            .where(StoryFrame.story_key.in_([story.story_key for story in stories]))
            .order_by(StoryFrame.story_key.asc(), StoryFrame.rank.asc(), StoryFrame.event_frame_id.asc())
        ).all()
        frame_ids_by_story: dict[str, list[str]] = {}
        for story_key, event_frame_id in frame_memberships:
            frame_ids_by_story.setdefault(story_key, []).append(event_frame_id)

        existing = [
            _ExistingStory(
                story_key=story.story_key,
                signature_json={
                    "event_type": story.event_type,
                    "signature_json": dict(story.anchor_json or {}),
                },
                frame_ids=tuple(frame_ids_by_story.get(story.story_key, [])),
                signature_token=self._signature_token(
                    {
                        "event_type": story.event_type,
                        "signature_json": dict(story.anchor_json or {}),
                    }
                ),
            )
            for story in stories
        ]
        return existing

    async def _resolve_story_keys(
        self,
        candidate_groups: list[_CandidateGroup],
        existing: list[_ExistingStory],
    ) -> list[_ResolvedStory]:
        resolved: list[_ResolvedStory] = []
        used_existing_keys: set[str] = set()
        existing_by_token: dict[str, list[_ExistingStory]] = {}
        for item in existing:
            existing_by_token.setdefault(item.signature_token, []).append(item)

        for group in candidate_groups:
            compatible = [
                item
                for item in existing_by_token.get(group.signature_token, [])
                if item.story_key not in used_existing_keys
            ]
            pick_key = str(uuid4())
            synopsis = self._build_default_synopsis(group.signature_json)

            if compatible:
                ratios = [(item, self._overlap_ratio(group.frame_ids, item.frame_ids)) for item in compatible]
                top_ratio = max(ratio for _, ratio in ratios)
                top_candidates = [item for item, ratio in ratios if ratio == top_ratio]
                if top_ratio >= 0.5:
                    if len(top_candidates) == 1:
                        pick_key = top_candidates[0].story_key
                    else:
                        tie = await self._run_tie_break(group, top_candidates)
                        choice = tie.choice
                        if choice.reuse_strict_story_key is not None:
                            candidate_keys = {item.story_key for item in top_candidates}
                            if choice.reuse_strict_story_key in candidate_keys:
                                pick_key = choice.reuse_strict_story_key
                        synopsis = choice.synopsis_zh.strip() or synopsis

            if pick_key in {item.story_key for item in compatible}:
                used_existing_keys.add(pick_key)

            resolved.append(
                _ResolvedStory(
                    story_key=pick_key,
                    signature_json=group.signature_json,
                    synopsis_zh=synopsis,
                    frame_ids=group.frame_ids,
                    article_ids=group.article_ids,
                    signature_token=group.signature_token,
                )
            )

        return sorted(resolved, key=lambda item: item.signature_token)

    def _replace_day_rows(
        self,
        session: Session,
        business_day: date,
        *,
        run_id: str,
        resolved: list[_ResolvedStory],
    ) -> list[Story]:
        existing_rows = list(
            session.scalars(select(Story).where(Story.business_date == business_day)).all()
        )
        existing_by_key = {item.story_key: item for item in existing_rows}
        existing_keys = set(existing_by_key.keys())
        resolved_keys = {item.story_key for item in resolved}
        reused_keys = existing_keys & resolved_keys
        obsolete_keys = existing_keys - resolved_keys

        if reused_keys:
            session.execute(delete(StoryFrame).where(StoryFrame.story_key.in_(reused_keys)))
            session.execute(delete(StoryArticle).where(StoryArticle.story_key.in_(reused_keys)))

        if obsolete_keys:
            session.execute(delete(Story).where(Story.story_key.in_(obsolete_keys)))

        stories_in_order: list[Story] = []
        frame_rows: list[StoryFrame] = []
        article_rows: list[StoryArticle] = []
        for story in resolved:
            event_type = str(story.signature_json.get("event_type", "")).strip() or "general"
            anchor_json = story.signature_json.get("signature_json", {})
            if not isinstance(anchor_json, dict):
                anchor_json = {}

            if story.story_key in existing_by_key:
                persisted = existing_by_key[story.story_key]
                persisted.business_date = business_day
                persisted.synopsis_zh = story.synopsis_zh
                persisted.event_type = event_type
                persisted.anchor_json = anchor_json
                persisted.article_membership_json = list(story.article_ids)
                persisted.created_run_id = run_id
                persisted.clustering_status = "done"
                persisted.clustering_error = None
            else:
                persisted = Story(
                    story_key=story.story_key,
                    business_date=business_day,
                    event_type=event_type,
                    synopsis_zh=story.synopsis_zh,
                    anchor_json=anchor_json,
                    article_membership_json=list(story.article_ids),
                    created_run_id=run_id,
                    clustering_status="done",
                    clustering_error=None,
                )
                session.add(persisted)

            stories_in_order.append(persisted)
            frame_rows.extend(
                [
                    StoryFrame(
                        story_key=persisted.story_key,
                        event_frame_id=frame_id,
                        rank=rank,
                    )
                    for rank, frame_id in enumerate(story.frame_ids)
                ]
            )
            article_rows.extend(
                [
                    StoryArticle(
                        story_key=persisted.story_key,
                        article_id=article_id,
                        rank=rank,
                    )
                    for rank, article_id in enumerate(story.article_ids)
                ]
            )

        session.add_all(frame_rows)
        session.add_all(article_rows)
        session.flush()
        for story in stories_in_order:
            session.expunge(story)
        return stories_in_order

    def _signature_token(self, signature_payload: dict) -> str:
        return json.dumps(signature_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    def _overlap_ratio(self, left: tuple[str, ...], right: tuple[str, ...]) -> float:
        left_set = set(left)
        right_set = set(right)
        if not left_set and not right_set:
            return 0.0
        union_size = len(left_set | right_set)
        if union_size == 0:
            return 0.0
        return len(left_set & right_set) / union_size

    async def _run_tie_break(
        self,
        group: _CandidateGroup,
        candidates: list[_ExistingStory],
    ) -> StrictStoryTieBreakSchema:
        client = self._get_client()
        payload = {
            "candidate_group": {
                "signature_json": group.signature_json,
                "frame_ids": list(group.frame_ids),
                "article_ids": list(group.article_ids),
            },
            "existing_candidates": [
                {
                    "strict_story_key": item.story_key,
                    "signature_json": item.signature_json,
                    "frame_ids": list(item.frame_ids),
                    "overlap_ratio": self._overlap_ratio(group.frame_ids, item.frame_ids),
                }
                for item in candidates
            ],
        }
        with self._rate_limiter.lease("strict_story_tie_break"):
            response = await client.chat.completions.create(
                model=STORY_SUMMARIZATION_MODEL_CONFIG.model_name,
                temperature=0,
                messages=[
                    {"role": "system", "content": build_strict_story_tiebreak_prompt()},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                response_format={"type": "json_object"},
            )
        raw_content = response.choices[0].message.content or "{}"
        return StrictStoryTieBreakSchema.model_validate_json(raw_content)

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            from openai import AsyncOpenAI

            api_key = STORY_SUMMARIZATION_MODEL_CONFIG.api_key
            if not api_key:
                raise RuntimeError("strict-story tie-break requires configured API key")
            self._client = AsyncOpenAI(
                api_key=api_key,
                base_url=STORY_SUMMARIZATION_MODEL_CONFIG.base_url,
                timeout=STORY_SUMMARIZATION_MODEL_CONFIG.timeout_seconds,
            )
        return self._client

    def _build_default_synopsis(self, signature_json: dict) -> str:
        event_type = str(signature_json.get("event_type", "")).strip()
        subject = signature_json.get("signature_json", {})
        if not isinstance(subject, dict):
            subject = {}
        event_type_label = {
            "runway_show": "时装秀事件动态",
            "brand_appointment": "品牌任命事件动态",
            "campaign_launch": "品牌企划事件动态",
            "store_opening": "门店拓展事件动态",
        }.get(event_type, "时尚行业事件动态")
        details: list[str] = []
        if subject.get("brand"):
            details.append(f"品牌{subject['brand']}")
        if subject.get("person"):
            details.append(f"人物{subject['person']}")
        if subject.get("season"):
            details.append(f"季别{subject['season']}")
        if subject.get("collection"):
            details.append(f"系列{subject['collection']}")
        if subject.get("place"):
            details.append(f"地点{subject['place']}")
        if details:
            candidate = f"{event_type_label}：" + "，".join(str(item) for item in details)
            try:
                return normalize_readable_synopsis_zh(candidate)
            except ValueError:
                return event_type_label
        return event_type_label
