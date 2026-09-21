import math

import pytest

from sourcelens.api.errors import DomainError
from sourcelens.config import Settings
from sourcelens.persistence.models import EMBEDDING_DIMENSIONS
from sourcelens.retrieval.embeddings import HashingEmbeddings, get_embedding_provider


def test_hashing_embeddings_are_deterministic_and_normalized():
    provider = HashingEmbeddings()
    first, second = provider.embed(["def add_one(number): return number + 1"] * 2)
    assert first == second
    assert len(first) == EMBEDDING_DIMENSIONS
    assert math.isclose(math.sqrt(sum(value * value for value in first)), 1.0, abs_tol=1e-9)


def test_hashing_embeddings_reward_shared_vocabulary():
    provider = HashingEmbeddings()
    related_a, related_b, unrelated = provider.embed(
        [
            "class OrderService: def create_order(self): ...",
            "def create_order(order): return OrderService().save(order)",
            "npm install react react-dom vite tailwind",
        ]
    )

    def cosine(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    assert cosine(related_a, related_b) > cosine(related_a, unrelated)


def test_empty_text_yields_a_zero_vector_without_dividing_by_zero():
    assert HashingEmbeddings().embed([""]) == [[0.0] * EMBEDDING_DIMENSIONS]


def test_get_embedding_provider_selects_hashing_by_default():
    provider = get_embedding_provider(Settings())
    assert isinstance(provider, HashingEmbeddings)


def test_get_embedding_provider_requires_an_api_key_for_voyage():
    settings = Settings(embedding_provider="voyage", voyage_api_key=None)
    with pytest.raises(DomainError, match="VOYAGE_API_KEY"):
        get_embedding_provider(settings)


def test_get_embedding_provider_rejects_unknown_providers():
    settings = Settings(embedding_provider="not-a-real-provider")
    with pytest.raises(DomainError, match="Unknown embedding provider"):
        get_embedding_provider(settings)
