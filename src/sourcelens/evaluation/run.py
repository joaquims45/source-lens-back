import argparse
import time
from statistics import mean
from typing import Any
from uuid import UUID

from sourcelens.config import get_settings
from sourcelens.evaluation.dataset import EvalQuestion, load_dataset
from sourcelens.evaluation.metrics import recall_at_k, reciprocal_rank
from sourcelens.persistence.database import session
from sourcelens.retrieval.embeddings import get_embedding_provider
from sourcelens.retrieval.rerank import OverlapReranker
from sourcelens.retrieval.service import Strategy, search

STRATEGIES: list[Strategy] = ["semantic", "lexical", "hybrid", "hybrid_rerank"]


def evaluate(
    analysis_id: UUID, dataset: list[EvalQuestion], strategy: Strategy, k: int
) -> dict[str, Any]:
    provider = get_embedding_provider(get_settings())
    reranker = OverlapReranker()
    recalls, ranks, latencies = [], [], []
    with session() as db:
        for item in dataset:
            started = time.perf_counter()
            results = search(
                db,
                analysis_id,
                item.question,
                embedding_provider=provider,
                reranker=reranker,
                strategy=strategy,
                k=k,
            )
            latencies.append(time.perf_counter() - started)
            paths = [result.path for result in results]
            recalls.append(recall_at_k(paths, item.expected_files, k))
            ranks.append(reciprocal_rank(paths, item.expected_files))
    return {
        "strategy": strategy,
        "questions": len(dataset),
        "recall_at_k": mean(recalls) if recalls else 0.0,
        "mrr": mean(ranks) if ranks else 0.0,
        "avg_latency_ms": mean(latencies) * 1000 if latencies else 0.0,
    }


def report(reports: list[dict[str, Any]], k: int) -> str:
    header = f"{'strategy':<16}{'recall@' + str(k):<12}{'mrr':<8}{'avg_latency_ms':<15}"
    lines = [header, "-" * len(header)]
    for row in reports:
        lines.append(
            f"{row['strategy']:<16}{row['recall_at_k']:<12.3f}{row['mrr']:<8.3f}"
            f"{row['avg_latency_ms']:<15.1f}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m sourcelens.evaluation.run",
        description="Measure retrieval quality (Recall@K, MRR, latency) for an ingested analysis.",
    )
    parser.add_argument("--analysis-id", required=True, type=UUID)
    parser.add_argument("--dataset", required=True, help="Path to a JSON eval dataset")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--strategy", choices=[*STRATEGIES, "all"], default="all")
    args = parser.parse_args()

    dataset = load_dataset(args.dataset)
    strategies = STRATEGIES if args.strategy == "all" else [args.strategy]
    reports = [evaluate(args.analysis_id, dataset, strategy, args.k) for strategy in strategies]
    print(report(reports, args.k))


if __name__ == "__main__":
    main()
