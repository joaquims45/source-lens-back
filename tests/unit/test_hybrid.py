from uuid import uuid4

from sourcelens.retrieval.hybrid import reciprocal_rank_fusion
from sourcelens.retrieval.lexical import ScoredChunk


def test_agreement_between_rankings_outranks_a_single_top_hit():
    shared, semantic_only, lexical_only = uuid4(), uuid4(), uuid4()
    semantic = [ScoredChunk(shared, 0.99), ScoredChunk(semantic_only, 0.9)]
    lexical = [ScoredChunk(shared, 0.2), ScoredChunk(lexical_only, 0.5)]

    fused = reciprocal_rank_fusion(semantic, lexical)

    assert fused[0].chunk_id == shared
    assert {item.chunk_id for item in fused} == {shared, semantic_only, lexical_only}


def test_fusion_respects_limit_and_is_score_ordered():
    ranking = [ScoredChunk(uuid4(), 1.0 - index * 0.1) for index in range(10)]

    fused = reciprocal_rank_fusion(ranking, limit=3)

    assert len(fused) == 3
    assert [item.score for item in fused] == sorted(
        (item.score for item in fused), reverse=True
    )


def test_empty_rankings_produce_no_results():
    assert reciprocal_rank_fusion([], []) == []
