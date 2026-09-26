import hashlib
import math
import re
from typing import Protocol

import httpx

from sourcelens.api.errors import DomainError
from sourcelens.config import Settings
from sourcelens.persistence.models import EMBEDDING_DIMENSIONS

TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+")


class EmbeddingProvider(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashingEmbeddings:
    """A deterministic, offline baseline: no API key, network call or model
    download required to run SourceLens locally end to end.

    Each identifier-like token is hashed into a fixed-size vector (a
    feature-hashing / "hashing trick" bag-of-tokens), then L2-normalized so
    cosine similarity behaves sensibly. This rewards literal vocabulary
    overlap and is intentionally not a substitute for a trained code-embedding
    model — swap in `VoyageEmbeddings` (or another provider behind this same
    Protocol) for real semantic recall, without changing any retrieval code.
    """

    name = f"hashing-v1-{EMBEDDING_DIMENSIONS}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * EMBEDDING_DIMENSIONS
        for token in TOKEN.findall(text.lower()):
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % EMBEDDING_DIMENSIONS
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return vector
        return [value / norm for value in vector]


class VoyageEmbeddings:
    """Voyage AI code embeddings, the production-grade provider named in the
    Milestone 0 design. Requires `VOYAGE_API_KEY`; the response dimension
    must match `EMBEDDING_DIMENSIONS`, which is fixed by migration.
    """

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model
        self.name = f"voyage:{model}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = httpx.post(
            "https://api.voyageai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"input": texts, "model": self.model, "input_type": "document"},
            timeout=30,
        )
        response.raise_for_status()
        embeddings = [item["embedding"] for item in response.json()["data"]]
        for embedding in embeddings:
            if len(embedding) != EMBEDDING_DIMENSIONS:
                raise DomainError(
                    "embedding_dimension_mismatch",
                    f"{self.name} returned {len(embedding)} dimensions, "
                    f"schema expects {EMBEDDING_DIMENSIONS}",
                    500,
                )
        return embeddings


class OpenAIEmbeddings:
    """OpenAI's embeddings API. Requires `OPENAI_API_KEY`. Requests exactly
    `EMBEDDING_DIMENSIONS` back via the `dimensions` parameter that
    `text-embedding-3-*` models support (truncating their native, larger
    embedding), so this fits the existing fixed-width `pgvector` column
    with no schema migration.
    """

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model
        self.name = f"openai:{model}:{EMBEDDING_DIMENSIONS}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = httpx.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"input": texts, "model": self.model, "dimensions": EMBEDDING_DIMENSIONS},
            timeout=30,
        )
        response.raise_for_status()
        embeddings = [item["embedding"] for item in response.json()["data"]]
        for embedding in embeddings:
            if len(embedding) != EMBEDDING_DIMENSIONS:
                raise DomainError(
                    "embedding_dimension_mismatch",
                    f"{self.name} returned {len(embedding)} dimensions, "
                    f"schema expects {EMBEDDING_DIMENSIONS}",
                    500,
                )
        return embeddings


def get_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "hashing":
        return HashingEmbeddings()
    if settings.embedding_provider == "voyage":
        if not settings.voyage_api_key:
            raise DomainError("embedding_provider_unconfigured", "VOYAGE_API_KEY is not set", 500)
        return VoyageEmbeddings(settings.voyage_api_key, settings.voyage_model)
    if settings.embedding_provider == "openai":
        if not settings.openai_api_key:
            raise DomainError("embedding_provider_unconfigured", "OPENAI_API_KEY is not set", 500)
        return OpenAIEmbeddings(settings.openai_api_key, settings.openai_embedding_model)
    raise DomainError(
        "embedding_provider_unknown",
        f"Unknown embedding provider {settings.embedding_provider!r}",
        500,
    )
