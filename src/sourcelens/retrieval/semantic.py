from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from sourcelens.persistence.models import Chunk
from sourcelens.retrieval.lexical import ScoredChunk


def semantic_search(
    db: Session, analysis_id: UUID, query_embedding: list[float], limit: int = 20
) -> list[ScoredChunk]:
    """Nearest-neighbor search over `code_chunks.embedding` by cosine
    distance. Exact (brute-force) search, per the Milestone 0 decision to
    introduce an ANN index only once recall measurements justify it.
    """
    distance = Chunk.embedding.cosine_distance(query_embedding)
    statement = (
        select(Chunk.id, distance.label("distance"))
        .where(Chunk.analysis_id == analysis_id, Chunk.embedding.is_not(None))
        .order_by(distance)
        .limit(limit)
    )
    return [
        ScoredChunk(chunk_id, 1.0 - distance) for chunk_id, distance in db.execute(statement).all()
    ]
