"""Cost calculator tests."""

import uuid
from decimal import Decimal

import pytest

from app.pipeline.cost import DEFAULT_PRICES, CostCalculator, ModelPrice, UnknownModelError
from app.pipeline.llm import Usage

MODEL = "claude-haiku-4-5-20251001"


def _usage(inp: int, out: int, cache_read: int = 0, cache_write: int = 0) -> Usage:
    return Usage(
        model=MODEL,
        input_tokens=inp,
        output_tokens=out,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_write,
    )


def test_cost_for_uses_price_table() -> None:
    calc = CostCalculator()
    # 1M input @ $1 + 1M output @ $5 = $6.00
    assert calc.cost_for(_usage(1_000_000, 1_000_000)) == Decimal("6.00")


def test_cost_includes_cache_tokens() -> None:
    calc = CostCalculator()
    # 1M cache read @ $0.10 + 1M cache write @ $1.25 = $1.35
    assert calc.cost_for(_usage(0, 0, cache_read=1_000_000, cache_write=1_000_000)) == Decimal("1.35")


def test_unknown_model_raises() -> None:
    calc = CostCalculator()
    with pytest.raises(UnknownModelError):
        calc.cost_for(Usage(model="mystery-model", input_tokens=1, output_tokens=1))


def test_listing_cost_sums_all_calls() -> None:
    calc = CostCalculator()
    # vision + content attempt = two calls
    usages = [_usage(1_000_000, 0), _usage(0, 1_000_000)]
    assert calc.listing_cost(usages) == Decimal("6.00")


def test_batch_cost_aggregates_per_listing() -> None:
    calc = CostCalculator()
    l1, l2 = uuid.uuid4(), uuid.uuid4()
    batch = calc.batch_cost(
        {
            l1: [_usage(1_000_000, 0)],  # $1.00
            l2: [_usage(0, 1_000_000)],  # $5.00
        }
    )
    assert batch.listing_count == 2
    assert batch.per_listing[l1] == Decimal("1.00")
    assert batch.per_listing[l2] == Decimal("5.00")
    assert batch.total_cost == Decimal("6.00")
    assert batch.total_input_tokens == 1_000_000
    assert batch.total_output_tokens == 1_000_000


def test_custom_price_table_overrides_default() -> None:
    cheap = {MODEL: ModelPrice(Decimal("0.50"), Decimal("0.50"), Decimal("0.625"), Decimal("0.05"))}
    calc = CostCalculator(prices=cheap)
    assert calc.cost_for(_usage(1_000_000, 1_000_000)) == Decimal("1.00")
    # default table is untouched
    assert DEFAULT_PRICES[MODEL].input == Decimal("1.00")
