"""Business-day bounded-context story clustering service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.config.llm_config import STORY_SUMMARIZATION_MODEL_CONFIG
from backend.app.models import Article, ArticleEventFrame, Story, StoryArticle, StoryFrame
from backend.app.prompts.story_cluster_judgment_prompt import build_story_cluster_judgment_prompt
from backend.app.schemas.llm.story_cluster_judgment import (
    StoryClusterGroup,
    StoryClusterJudgmentSchema,
)
from backend.app.service.event_frame_extraction_service import build_event_frame_signature_json
from backend.app.service.llm_rate_limiter import LlmRateLimiter

if TYPE_CHECKING:
    from openai import AsyncOpenAI


@dataclass(frozen=True)
class _FrameCard:
    event_frame_id: str
    article_id: str
    source_name: str
    source_lang: str
    event_type: str
    brand: str
    person: str
    collection: str
    season: str
    place: str
    action_text: str
    object_text: str
    evidence_snippets: tuple[str, ...]
    anchor_json: dict[str, str]

    def anchor_tokens(self) -> tuple[str, ...]:
        tokens: list[str] = []
        for key in ("brand", "person", "collection", "season", "place"):
            value = self.anchor_json.get(key, "")
            if value:
                tokens.append(f"{key}:{value.casefold()}")
        return tuple(tokens)

    def to_payload(self) -> dict[str, object]:
        return {
            "event_frame_id": self.event_frame_id,
            "article_id": self.article_id,
            "source_name": self.source_name,
            "source_lang": self.source_lang,
            "event_type": self.event_type,
            "brand": self.brand,
            "person": self.person,
            "collection": self.collection,
            "season": self.season,
            "place": self.place,
            "action_text": self.action_text,
            "object_text": self.object_text,
            "evidence_snippets": list(self.evidence_snippets),
            "anchor_json": dict(self.anchor_json),
        }


@dataclass(frozen=True)
class _JudgedGroup:
    member_event_frame_ids: tuple[str, ...]
    synopsis_zh: str
    event_type: str
    anchor_json: dict


@dataclass(frozen=True)
class _ResolvedStoryPlan:
    member_event_frame_ids: tuple[str, ...]
    article_ids: tuple[str, ...]
    synopsis_zh: str
    event_type: str
    anchor_json: dict


class StoryClusteringService:
    """Cluster one business day of event frames into immutable stories."""

    def __init__(
        self,
        *,
        client: AsyncOpenAI | None = None,
        rate_limiter: LlmRateLimiter | None = None,
        max_window_size: int = 8,
    ) -> None:
        self._client = client
        self._rate_limiter = rate_limiter or LlmRateLimiter()
        self._max_window_size = max_window_size

    async def cluster_business_day(
        self,
        session: Session,
        business_day: date,
        *,
        run_id: str,
    ) -> list[Story]:
        frame_cards = self._load_frame_cards(session, business_day)
        if not frame_cards:
            return []

        windows = self._build_candidate_windows(frame_cards)
        judged_groups = await self._judge_candidate_windows(windows)
        if not judged_groups:
            raise RuntimeError(
                f"story clustering produced zero stories for non-empty input: business_day={business_day.isoformat()}"
            )

        return self._replace_day_rows(
            session,
            business_day,
            run_id=run_id,
            frame_cards=frame_cards,
            judged_groups=judged_groups,
        )

    def _load_frame_cards(self, session: Session, business_day: date) -> list[_FrameCard]:
        rows = session.execute(
            select(ArticleEventFrame, Article)
            .join(Article, Article.article_id == ArticleEventFrame.article_id)
            .where(ArticleEventFrame.business_date == business_day)
            .order_by(ArticleEventFrame.event_frame_id.asc())
        ).all()
        return [self._build_frame_card(frame, article) for frame, article in rows]

    def _build_frame_card(self, frame: ArticleEventFrame, article: Article) -> _FrameCard:
        subject_json = frame.subject_json if isinstance(frame.subject_json, dict) else {}
        anchor_json = build_event_frame_signature_json(
            subject_json=subject_json,
            signature_json=frame.signature_json,
            place_text=frame.place_text,
            collection_text=frame.collection_text,
            season_text=frame.season_text,
        )
        return _FrameCard(
            event_frame_id=frame.event_frame_id,
            article_id=frame.article_id,
            source_name=article.source_name.strip(),
            source_lang=article.source_lang.strip(),
            event_type=frame.event_type.strip(),
            brand=self._clean_anchor_value(subject_json.get("brand")),
            person=self._clean_anchor_value(subject_json.get("person")),
            collection=self._clean_anchor_value(frame.collection_text),
            season=self._clean_anchor_value(frame.season_text),
            place=self._clean_anchor_value(frame.place_text),
            action_text=frame.action_text.strip(),
            object_text=frame.object_text.strip(),
            evidence_snippets=self._extract_evidence_snippets(frame.evidence_json),
            anchor_json=anchor_json,
        )

    def _build_candidate_windows(self, frame_cards: list[_FrameCard]) -> list[tuple[_FrameCard, ...]]:
        cards_by_id = {card.event_frame_id: card for card in frame_cards}
        ids_by_token: dict[str, list[str]] = {}
        for card in frame_cards:
            for token in card.anchor_tokens():
                ids_by_token.setdefault(token, []).append(card.event_frame_id)

        windows: list[tuple[_FrameCard, ...]] = []
        seen_windows: set[tuple[str, ...]] = set()
        for card in frame_cards:
            scored_candidates: dict[str, int] = {}
            for token in card.anchor_tokens():
                for candidate_id in ids_by_token.get(token, []):
                    if candidate_id == card.event_frame_id:
                        continue
                    scored_candidates[candidate_id] = scored_candidates.get(candidate_id, 0) + 1

            ranked_ids = [
                candidate_id
                for candidate_id, _ in sorted(
                    scored_candidates.items(),
                    key=lambda item: (-item[1], item[0]),
                )
            ]
            picked_ids = [card.event_frame_id, *ranked_ids[: max(self._max_window_size - 1, 0)]]
            window_key = tuple(sorted(set(picked_ids)))
            if window_key in seen_windows:
                continue
            seen_windows.add(window_key)
            windows.append(tuple(cards_by_id[event_frame_id] for event_frame_id in window_key))
        return windows

    async def _judge_candidate_windows(
        self,
        windows: list[tuple[_FrameCard, ...]],
    ) -> list[_JudgedGroup]:
        judged_groups: dict[tuple[str, ...], _JudgedGroup] = {}
        for window in windows:
            schema = await self._run_story_cluster_judgment(window)
            valid_ids = {card.event_frame_id for card in window}
            for group in schema.groups:
                normalized_member_ids = self._normalize_member_ids(group, valid_ids)
                judged_groups.setdefault(
                    normalized_member_ids,
                    _JudgedGroup(
                        member_event_frame_ids=normalized_member_ids,
                        synopsis_zh=group.synopsis_zh.strip(),
                        event_type=group.event_type.strip(),
                        anchor_json=self._normalize_anchor_json(group.anchor_json),
                    ),
                )
        return sorted(
            judged_groups.values(),
            key=lambda item: (len(item.member_event_frame_ids), item.member_event_frame_ids),
        )

    async def _run_story_cluster_judgment(
        self,
        window: tuple[_FrameCard, ...],
    ) -> StoryClusterJudgmentSchema:
        client = self._get_client()
        payload = {
            "candidate_frames": [card.to_payload() for card in window],
        }
        with self._rate_limiter.lease("story_cluster_judgment"):
            response = await client.chat.completions.create(
                model=STORY_SUMMARIZATION_MODEL_CONFIG.model_name,
                temperature=0,
                messages=[
                    {"role": "system", "content": build_story_cluster_judgment_prompt()},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                response_format={"type": "json_object"},
            )
        raw_content = response.choices[0].message.content or "{}"
        return StoryClusterJudgmentSchema.model_validate_json(raw_content)

    def _replace_day_rows(
        self,
        session: Session,
        business_day: date,
        *,
        run_id: str,
        frame_cards: list[_FrameCard],
        judged_groups: list[_JudgedGroup],
    ) -> list[Story]:
        card_by_id = {card.event_frame_id: card for card in frame_cards}
        story_plans = self._resolve_story_plans(card_by_id, judged_groups)

        existing_story_keys = list(
            session.scalars(
                select(Story.story_key).where(Story.business_date == business_day).order_by(Story.story_key.asc())
            ).all()
        )
        if existing_story_keys:
            session.execute(delete(StoryFrame).where(StoryFrame.story_key.in_(existing_story_keys)))
            session.execute(delete(StoryArticle).where(StoryArticle.story_key.in_(existing_story_keys)))
            session.execute(delete(Story).where(Story.story_key.in_(existing_story_keys)))

        stories: list[Story] = []
        frame_rows: list[StoryFrame] = []
        article_rows: list[StoryArticle] = []
        for story_plan in story_plans:
            story = Story(
                business_date=business_day,
                event_type=story_plan.event_type,
                synopsis_zh=story_plan.synopsis_zh,
                anchor_json=story_plan.anchor_json,
                article_membership_json=list(story_plan.article_ids),
                created_run_id=run_id,
                clustering_status="done",
                clustering_error=None,
            )
            session.add(story)
            session.flush()
            stories.append(story)
            frame_rows.extend(
                StoryFrame(
                    story_key=story.story_key,
                    event_frame_id=event_frame_id,
                    rank=rank,
                )
                for rank, event_frame_id in enumerate(story_plan.member_event_frame_ids)
            )
            article_rows.extend(
                StoryArticle(
                    story_key=story.story_key,
                    article_id=article_id,
                    rank=rank,
                )
                for rank, article_id in enumerate(story_plan.article_ids)
            )

        session.add_all(frame_rows)
        session.add_all(article_rows)
        session.flush()
        for story in stories:
            session.expunge(story)
        return stories

    def _resolve_story_plans(
        self,
        card_by_id: dict[str, _FrameCard],
        judged_groups: list[_JudgedGroup],
    ) -> list[_ResolvedStoryPlan]:
        merged_groups = self._merge_overlapping_groups(judged_groups)
        resolved: list[_ResolvedStoryPlan] = []
        for member_ids in merged_groups:
            member_id_set = set(member_ids)
            matching_groups = [
                group
                for group in judged_groups
                if set(group.member_event_frame_ids).issubset(member_id_set)
            ]
            chosen_group = max(
                matching_groups,
                key=lambda group: (len(group.member_event_frame_ids), group.member_event_frame_ids),
            )
            article_ids: list[str] = []
            seen_article_ids: set[str] = set()
            for event_frame_id in member_ids:
                article_id = card_by_id[event_frame_id].article_id
                if article_id in seen_article_ids:
                    continue
                seen_article_ids.add(article_id)
                article_ids.append(article_id)
            resolved.append(
                _ResolvedStoryPlan(
                    member_event_frame_ids=member_ids,
                    article_ids=tuple(article_ids),
                    synopsis_zh=chosen_group.synopsis_zh,
                    event_type=chosen_group.event_type,
                    anchor_json=chosen_group.anchor_json,
                )
            )

        return sorted(
            resolved,
            key=lambda item: (
                item.member_event_frame_ids[0],
                tuple(card_by_id[event_frame_id].article_id for event_frame_id in item.member_event_frame_ids),
            ),
        )

    def _merge_overlapping_groups(self, judged_groups: list[_JudgedGroup]) -> list[tuple[str, ...]]:
        adjacency: dict[str, set[str]] = {}
        for group in judged_groups:
            members = group.member_event_frame_ids
            for member in members:
                adjacency.setdefault(member, set()).update(members)

        components: list[tuple[str, ...]] = []
        visited: set[str] = set()
        for event_frame_id in sorted(adjacency):
            if event_frame_id in visited:
                continue
            stack = [event_frame_id]
            component: set[str] = set()
            while stack:
                current = stack.pop()
                if current in visited:
                    continue
                visited.add(current)
                component.add(current)
                stack.extend(sorted(adjacency[current] - visited))
            components.append(tuple(sorted(component)))
        return components

    def _normalize_member_ids(
        self,
        group: StoryClusterGroup,
        valid_ids: set[str],
    ) -> tuple[str, ...]:
        member_ids: list[str] = []
        seen_ids: set[str] = set()
        for event_frame_id in group.member_event_frame_ids:
            normalized_id = event_frame_id.strip()
            if not normalized_id:
                raise ValueError("story cluster judgment returned blank event_frame_id")
            if normalized_id not in valid_ids:
                raise ValueError(
                    f"story cluster judgment returned unknown event_frame_id: {normalized_id}"
                )
            if normalized_id in seen_ids:
                continue
            seen_ids.add(normalized_id)
            member_ids.append(normalized_id)

        seed_event_frame_id = group.seed_event_frame_id.strip()
        if seed_event_frame_id not in valid_ids:
            raise ValueError(
                f"story cluster judgment returned unknown seed_event_frame_id: {seed_event_frame_id}"
            )
        if seed_event_frame_id not in seen_ids:
            member_ids.insert(0, seed_event_frame_id)
        if not member_ids:
            raise ValueError("story cluster judgment returned zero member_event_frame_ids")
        return tuple(sorted(member_ids))

    def _normalize_anchor_json(self, payload: object) -> dict:
        if not isinstance(payload, dict):
            return {}
        normalized: dict[str, str] = {}
        for key, value in payload.items():
            key_text = str(key).strip()
            value_text = self._clean_anchor_value(value)
            if key_text and value_text:
                normalized[key_text] = value_text
        return normalized

    def _extract_evidence_snippets(self, evidence_json: object) -> tuple[str, ...]:
        if not isinstance(evidence_json, list):
            return ()
        snippets: list[str] = []
        seen_snippets: set[str] = set()
        for item in evidence_json:
            if not isinstance(item, dict):
                continue
            for key in ("quote", "snippet", "text"):
                value = item.get(key)
                if not isinstance(value, str):
                    continue
                snippet = value.strip()
                if not snippet or snippet in seen_snippets:
                    continue
                seen_snippets.add(snippet)
                snippets.append(snippet)
                break
            if len(snippets) == 2:
                break
        return tuple(snippets)

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            from openai import AsyncOpenAI

            api_key = STORY_SUMMARIZATION_MODEL_CONFIG.api_key
            if not api_key:
                raise RuntimeError("story clustering requires configured API key")
            self._client = AsyncOpenAI(
                api_key=api_key,
                base_url=STORY_SUMMARIZATION_MODEL_CONFIG.base_url,
                timeout=STORY_SUMMARIZATION_MODEL_CONFIG.timeout_seconds,
            )
        return self._client

    def _clean_anchor_value(self, value: object) -> str:
        if not isinstance(value, str):
            return ""
        return value.strip()
