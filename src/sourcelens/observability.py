"""Structured logging helpers for the retrieval and agent hot paths.

Plain structlog calls today (JSON lines, correlated by the request_id the
main middleware already binds into contextvars — see PLAN.MD's
observability requirements: request_id, model, tools called, retrieved
chunks/scores, tokens, latencies, estimated cost). Every field logged here
is also a natural OpenTelemetry span attribute, so wiring in real tracing
later is a matter of wrapping these call sites in spans, not redesigning
what gets recorded.
"""

import structlog

logger = structlog.get_logger()

# Illustrative, approximate pricing (USD per 1M tokens) for a rough cost
# estimate in logs — not billing-accurate, and deliberately kept in one
# small table rather than guessed inline. An unrecognized model logs its
# token counts without a cost estimate rather than a fabricated number.
MODEL_PRICING_PER_MILLION_TOKENS: dict[str, tuple[float, float]] = {
    "claude-sonnet-5": (3.0, 15.0),
    "claude-opus-5": (15.0, 75.0),
    "claude-haiku-4-5-20251001": (0.8, 4.0),
    "claude-fable-5-1": (3.0, 15.0),
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    pricing = MODEL_PRICING_PER_MILLION_TOKENS.get(model)
    if pricing is None:
        return None
    input_price, output_price = pricing
    return (input_tokens / 1_000_000) * input_price + (output_tokens / 1_000_000) * output_price


def log_retrieval(
    *,
    analysis_id: str,
    strategy: str,
    query: str,
    candidate_count: int,
    top_scores: dict[str, float | None],
    latency_ms: float,
) -> None:
    logger.info(
        "retrieval",
        repository_id=analysis_id,
        strategy=strategy,
        query_chars=len(query),
        candidates=candidate_count,
        top_scores=top_scores,
        retrieval_latency_ms=round(latency_ms, 2),
    )


def log_agent_turn(
    *,
    analysis_id: str,
    conversation_id: str,
    model: str,
    tools_called: list[str],
    iterations: int,
    input_tokens: int,
    output_tokens: int,
    generation_latency_ms: float,
    total_latency_ms: float,
) -> None:
    cost = estimate_cost_usd(model, input_tokens, output_tokens)
    logger.info(
        "agent_turn",
        repository_id=analysis_id,
        conversation_id=conversation_id,
        model=model,
        tools_called=tools_called,
        iterations=iterations,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        generation_latency_ms=round(generation_latency_ms, 2),
        total_latency_ms=round(total_latency_ms, 2),
        estimated_cost_usd=round(cost, 6) if cost is not None else None,
    )
