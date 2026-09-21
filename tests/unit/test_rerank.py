from uuid import uuid4

from sourcelens.retrieval.rerank import OverlapReranker, RerankCandidate


def test_overlap_reranker_prefers_higher_token_overlap_with_the_query():
    relevant, noisy = uuid4(), uuid4()
    candidates = [
        RerankCandidate(noisy, "npm install react react-dom vite tailwind lucide-react"),
        RerankCandidate(relevant, "class OrderService: def create_order(self): pass"),
    ]

    reranked = OverlapReranker().rerank("create_order OrderService", candidates, limit=2)

    assert reranked[0].chunk_id == relevant
    assert reranked[0].score > reranked[1].score


def test_overlap_reranker_scores_empty_content_as_zero_without_dividing_by_zero():
    candidate = RerankCandidate(uuid4(), "")
    result = OverlapReranker().rerank("anything", [candidate], limit=1)
    assert result == [type(result[0])(candidate.chunk_id, 0.0)]


def test_overlap_reranker_respects_limit():
    candidates = [RerankCandidate(uuid4(), f"token{i}") for i in range(5)]
    assert len(OverlapReranker().rerank("token", candidates, limit=2)) == 2
