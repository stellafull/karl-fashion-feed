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
from backend.app.models import ArticleEventFrame, StrictStory, StrictStoryArticle, StrictStoryFrame
from backend.app.prompts.strict_story_tiebreak_prompt import build_strict_story_tiebreak_prompt
from backend.app.schemas.llm.strict_story_tiebreak import StrictStoryTieBreakSchema

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
    strict_story_key: str
    signature_json: dict
    frame_ids: tuple[str, ...]
    signature_token: str


@dataclass(frozen=True)
class _ResolvedStory:
    strict_story_key: str
    signature_json: dict
    synopsis_zh: str
    frame_ids: tuple[str, ...]
    article_ids: tuple[str, ...]
    signature_token: str


class StrictStoryPackingService:
    """Pack one business-day event-frame set into immutable strict stories."""

    def __init__(self, *, client: AsyncOpenAI | None = None) -> None:
        self._client = client

    async def pack_business_day(
        self,
        session: Session,
        business_day: date,
        *,
        run_id: str,
    ) -> list[StrictStory]:
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
                select(StrictStory)
                .where(StrictStory.business_date == business_day)
                .order_by(StrictStory.strict_story_key.asc())
            ).all()
        )
        if not stories:
            return []

        story_keys = [story.strict_story_key for story in stories]
        frame_rows = session.execute(
            select(StrictStoryFrame.strict_story_key, StrictStoryFrame.event_frame_id).where(
                StrictStoryFrame.strict_story_key.in_(story_keys)
            )
        ).all()
        frame_map: dict[str, list[str]] = {key: [] for key in story_keys}
        for strict_story_key, event_frame_id in frame_rows:
            frame_map[strict_story_key].append(event_frame_id)

        existing = [
            _ExistingStory(
                strict_story_key=story.strict_story_key,
                signature_json=dict(story.signature_json or {}),
                frame_ids=tuple(sorted(frame_map[story.strict_story_key])),
                signature_token=self._signature_token(dict(story.signature_json or {})),
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
                if item.strict_story_key not in used_existing_keys
            ]
            pick_key = str(uuid4())
            synopsis = self._build_default_synopsis(group.signature_json)

            if compatible:
                ratios = [(item, self._overlap_ratio(group.frame_ids, item.frame_ids)) for item in compatible]
                top_ratio = max(ratio for _, ratio in ratios)
                top_candidates = [item for item, ratio in ratios if ratio == top_ratio]
                if top_ratio >= 0.5:
                    if len(top_candidates) == 1:
                        pick_key = top_candidates[0].strict_story_key
                    else:
                        tie = await self._run_tie_break(group, top_candidates)
                        choice = tie.choice
                        if choice.reuse_strict_story_key is not None:
                            candidate_keys = {item.strict_story_key for item in top_candidates}
                            if choice.reuse_strict_story_key in candidate_keys:
                                pick_key = choice.reuse_strict_story_key
                        synopsis = choice.synopsis_zh.strip() or synopsis

            if pick_key in {item.strict_story_key for item in compatible}:
                used_existing_keys.add(pick_key)

            resolved.append(
                _ResolvedStory(
                    strict_story_key=pick_key,
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
    ) -> list[StrictStory]:
        existing_rows = list(
            session.scalars(select(StrictStory).where(StrictStory.business_date == business_day)).all()
        )
        existing_by_key = {item.strict_story_key: item for item in existing_rows}
        existing_keys = set(existing_by_key.keys())
        resolved_keys = {item.strict_story_key for item in resolved}
        reused_keys = existing_keys & resolved_keys
        obsolete_keys = existing_keys - resolved_keys

        if reused_keys:
            session.execute(delete(StrictStoryFrame).where(StrictStoryFrame.strict_story_key.in_(reused_keys)))
            session.execute(delete(StrictStoryArticle).where(StrictStoryArticle.strict_story_key.in_(reused_keys)))

        if obsolete_keys:
            session.execute(delete(StrictStory).where(StrictStory.strict_story_key.in_(obsolete_keys)))

        stories_in_order: list[StrictStory] = []
        frame_rows: list[StrictStoryFrame] = []
        article_rows: list[StrictStoryArticle] = []
        for story in resolved:
            if story.strict_story_key in existing_by_key:
                persisted = existing_by_key[story.strict_story_key]
                persisted.business_date = business_day
                persisted.synopsis_zh = story.synopsis_zh
                persisted.signature_json = story.signature_json
                persisted.created_run_id = run_id
                persisted.packing_status = "done"
                persisted.packing_error = None
            else:
                persisted = StrictStory(
                    strict_story_key=story.strict_story_key,
                    business_date=business_day,
                    synopsis_zh=story.synopsis_zh,
                    signature_json=story.signature_json,
                    created_run_id=run_id,
                    packing_status="done",
                    packing_error=None,
                )
                session.add(persisted)

            stories_in_order.append(persisted)
            frame_rows.extend(
                [
                    StrictStoryFrame(
                        strict_story_key=persisted.strict_story_key,
                        event_frame_id=frame_id,
                        rank=rank,
                    )
                    for rank, frame_id in enumerate(story.frame_ids)
                ]
            )
            article_rows.extend(
                [
                    StrictStoryArticle(
                        strict_story_key=persisted.strict_story_key,
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
                    "strict_story_key": item.strict_story_key,
                    "signature_json": item.signature_json,
                    "frame_ids": list(item.frame_ids),
                    "overlap_ratio": self._overlap_ratio(group.frame_ids, item.frame_ids),
                }
                for item in candidates
            ],
        }
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
            "runway_show": "时装秀动态",
            "brand_appointment": "品牌任命动态",
            "campaign_launch": "品牌企划动态",
            "store_opening": "门店拓展动态",
        }.get(event_type, "时尚事件动态")
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
            return f"{event_type_label}：" + "，".join(str(item) for item in details)
        return event_type_label
