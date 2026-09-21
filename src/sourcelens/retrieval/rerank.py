import re
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sourcelens.retrieval.lexical import ScoredChunk

TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+")


@dataclass(frozen=True)
class RerankCandidate:
    chunk_id: UUID
    content: str


class Reranker(Protocol):
    name: str

    def rerank(
        self, query: str, candidates: list[RerankCandidate], limit: int
    ) -> list[ScoredChunk]: ...


class OverlapReranker:
    """Rescore hybrid candidates by the fraction of each chunk's tokens that
    appear in the query.

    This is a deliberately simple baseline reranker, not a trained
    cross-encoder: it needs no model download or external API, so the full
    hybrid + rerank pipeline runs and is measurable offline. It is behind the
    same `Reranker` protocol as any future cross-encoder or hosted rerank API
    (e.g. Voyage rerank), so swapping it later doesn't touch retrieval code.
    """

    name = "overlap-v1"

    def rerank(
        self, query: str, candidates: list[RerankCandidate], limit: int
    ) -> list[ScoredChunk]:
        query_tokens = set(TOKEN.findall(query.lower()))
        scored = []
        for candidate in candidates:
            content_tokens = TOKEN.findall(candidate.content.lower())
            score = (
                sum(1 for token in content_tokens if token in query_tokens) / len(content_tokens)
                if content_tokens
                else 0.0
            )
            scored.append(ScoredChunk(candidate.chunk_id, score))
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:limit]
