def recall_at_k(retrieved_paths: list[str], expected_files: list[str], k: int) -> float:
    """Fraction of `expected_files` that appear anywhere in the top `k`
    retrieved paths. Duplicate retrieved paths (multiple chunks per file)
    are deduplicated so a file isn't rewarded twice for being chunked finely.
    """
    if not expected_files:
        return 0.0
    top_k = set(retrieved_paths[:k])
    hits = sum(1 for expected in expected_files if expected in top_k)
    return hits / len(expected_files)


def reciprocal_rank(retrieved_paths: list[str], expected_files: list[str]) -> float:
    """1/rank of the first retrieved path that is one of the expected files,
    or 0 if none of them were retrieved at all.
    """
    expected = set(expected_files)
    for rank, path in enumerate(retrieved_paths, start=1):
        if path in expected:
            return 1.0 / rank
    return 0.0
