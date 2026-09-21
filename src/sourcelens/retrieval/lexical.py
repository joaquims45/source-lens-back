from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import ColumnElement, func, literal_column, select
from sqlalchemy.orm import Session

from sourcelens.persistence.models import Chunk


@dataclass(frozen=True)
class ScoredChunk:
    chunk_id: UUID
    score: float


def lexical_search(
    db: Session, analysis_id: UUID, query: str, limit: int = 20
) -> list[ScoredChunk]:
    """Full-text search over `code_chunks.content_tsv` (a GIN-indexed, stored
    `tsvector` column — see the `add chunk embeddings` migration), ranked with
    `ts_rank_cd`. This is the lexical half of hybrid retrieval: it finds exact
    identifier/keyword matches that embeddings alone can miss.

    `content_tsv` is a database-generated column with no ORM attribute (it
    must never be written through the model), so it is referenced here by
    name via `literal_column` instead.
    """
    content_tsv: ColumnElement[str] = literal_column("content_tsv")
    tsquery = func.plainto_tsquery("english", query)
    rank = func.ts_rank_cd(content_tsv, tsquery).label("score")
    statement = (
        select(Chunk.id, rank)
        .where(Chunk.analysis_id == analysis_id, content_tsv.op("@@")(tsquery))
        .order_by(rank.desc())
        .limit(limit)
    )
    return [ScoredChunk(chunk_id, score) for chunk_id, score in db.execute(statement).all()]
