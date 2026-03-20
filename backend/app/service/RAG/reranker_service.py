"""Cross-encoder reranker service for retrieval-core relevance sorting."""

from __future__ import annotations

from dashscope import TextReRank

from backend.app.config.embedding_config import RERANKER_CONFIG


class RerankerService:
    """Encapsulate DashScope text reranking for retrieval lanes."""

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
        """Rerank candidate documents for one retrieval lane."""
        if not query.strip():
            raise ValueError("rerank query must not be empty")
        if not documents:
            return []
        if top_n <= 0:
            raise ValueError("top_n must be greater than 0")

        response = TextReRank.call(
            model=RERANKER_CONFIG.model_name,
            query=query,
            documents=documents,
            top_n=min(top_n, len(documents)),
            return_documents=False,
            api_key=RERANKER_CONFIG.api_key,
        )
        if int(response.status_code) != 200:
            raise ValueError(
                "dashscope rerank failed: "
                f"status={response.status_code} code={response.code} message={response.message}"
            )
        return [
            (int(result.index), float(result.relevance_score))
            for result in response.output.results or []
        ]
