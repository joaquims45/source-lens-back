from sourcelens.observability import estimate_cost_usd


def test_estimate_cost_uses_known_model_pricing():
    cost = estimate_cost_usd("claude-sonnet-5", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == 3.0 + 15.0


def test_estimate_cost_is_none_for_an_unrecognized_model():
    assert estimate_cost_usd("some-future-model", input_tokens=100, output_tokens=50) is None


def test_estimate_cost_scales_linearly_with_tokens():
    cost = estimate_cost_usd("claude-haiku-4-5-20251001", input_tokens=500_000, output_tokens=0)
    assert cost == 0.4
