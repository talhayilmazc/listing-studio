"""Per-listing and per-batch cost from token usage and a configurable price table.

Cost instrumentation is mandatory: every listing records what it cost to produce,
and batches expose an aggregate. Prices are USD per 1M tokens and can be overridden
(defaults track the published Haiku 4.5 rates).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from app.pipeline.llm import Usage

_MILLION = Decimal(1_000_000)


class UnknownModelError(Exception):
    """No price entry for a model that was used."""


@dataclass(frozen=True)
class ModelPrice:
    """USD per 1M tokens for each token class."""

    input: Decimal
    output: Decimal
    cache_write: Decimal  # 1.25x input on Anthropic
    cache_read: Decimal  # 0.1x input on Anthropic


def _haiku_price() -> ModelPrice:
    return ModelPrice(
        input=Decimal("1.00"),
        output=Decimal("5.00"),
        cache_write=Decimal("1.25"),
        cache_read=Decimal("0.10"),
    )


# Default table; callers may pass their own to CostCalculator.
DEFAULT_PRICES: dict[str, ModelPrice] = {
    "claude-haiku-4-5-20251001": _haiku_price(),
    "claude-haiku-4-5": _haiku_price(),
    # Claude Sonnet 5: $2 / $10 per million tokens; cache write 1.25x, read 0.1x.
    "claude-sonnet-5": ModelPrice(
        input=Decimal("2.00"),
        output=Decimal("10.00"),
        cache_write=Decimal("2.50"),
        cache_read=Decimal("0.20"),
    ),
}


@dataclass
class BatchCost:
    total_cost: Decimal
    total_input_tokens: int
    total_output_tokens: int
    listing_count: int
    per_listing: dict[uuid.UUID, Decimal] = field(default_factory=dict)


class CostCalculator:
    def __init__(self, prices: Mapping[str, ModelPrice] | None = None) -> None:
        self._prices = dict(prices) if prices is not None else dict(DEFAULT_PRICES)

    def cost_for(self, usage: Usage) -> Decimal:
        """Cost of a single model call."""
        price = self._prices.get(usage.model)
        if price is None:
            raise UnknownModelError(usage.model)
        total = (
            usage.input_tokens * price.input
            + usage.output_tokens * price.output
            + usage.cache_creation_input_tokens * price.cache_write
            + usage.cache_read_input_tokens * price.cache_read
        )
        return total / _MILLION

    def listing_cost(self, usages: Iterable[Usage]) -> Decimal:
        """Cost of one listing = every model call that produced it (vision + retries)."""
        return sum((self.cost_for(u) for u in usages), Decimal("0"))

    def batch_cost(self, listing_usages: Mapping[uuid.UUID, Iterable[Usage]]) -> BatchCost:
        """Aggregate cost across a batch, keyed by listing id."""
        per_listing: dict[uuid.UUID, Decimal] = {}
        total = Decimal("0")
        total_in = 0
        total_out = 0
        for listing_id, usages in listing_usages.items():
            materialized = list(usages)
            per_listing[listing_id] = self.listing_cost(materialized)
            total += per_listing[listing_id]
            total_in += sum(u.input_tokens for u in materialized)
            total_out += sum(u.output_tokens for u in materialized)
        return BatchCost(
            total_cost=total,
            total_input_tokens=total_in,
            total_output_tokens=total_out,
            listing_count=len(per_listing),
            per_listing=per_listing,
        )
