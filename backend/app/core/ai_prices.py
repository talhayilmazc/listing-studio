"""The price table AI calls are costed with: defaults, changed in the admin panel.

USD per million tokens, one rate per billing class, as the provider bills them:

- ``input``: prompt tokens that were not cached
- ``output``: everything the model wrote (its thinking included)
- ``cache_write``: tokens written to the 5-minute cache (1.25x input)
- ``cache_write_1h``: tokens written to the 1-hour cache (2x input)
- ``cache_read``: tokens served from the cache

The defaults are the published first-party rates as of 2026-09-25. An admin can
change a model's rates or add a model (``app_setting`` key ``ai_prices``); the
stored table is laid over the defaults. A call is costed when it is recorded,
so changing a price does not rewrite history; a call whose model had no price
is stored with no cost and costed when a price is given.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppSetting

KEY = "ai_prices"
FIELDS = ("input", "output", "cache_write", "cache_write_1h", "cache_read")
_MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class Price:
    input: Decimal
    output: Decimal
    cache_write: Decimal
    cache_write_1h: Decimal
    cache_read: Decimal

    def as_strings(self) -> dict[str, str]:
        return {name: str(getattr(self, name)) for name in FIELDS}


def _price(input_: str, output: str, cache_read: str) -> Price:
    base = Decimal(input_)
    return Price(base, Decimal(output), base * Decimal("1.25"), base * 2, Decimal(cache_read))


DEFAULTS: dict[str, Price] = {
    "claude-haiku-4-5": _price("1.00", "5.00", "0.10"),
    "claude-sonnet-5": _price("2.00", "10.00", "0.20"),
    "claude-sonnet-5-5": _price("2.00", "10.00", "0.20"),
    "claude-opus-5": _price("5.00", "25.00", "0.50"),
    "claude-opus-5-5": _price("4.00", "20.00", "0.20"),
}


def validate(raw: dict[str, Any]) -> dict[str, str]:
    """One model's rates as strings, or ValueError naming what is wrong."""
    out: dict[str, str] = {}
    for name in FIELDS:
        try:
            value = Decimal(str(raw.get(name, "")).strip().replace(",", "."))
        except InvalidOperation:
            raise ValueError(f"{name.replace('_', ' ')} must be a number") from None
        if not value.is_finite() or value < 0 or value > 10_000:
            raise ValueError(f"{name.replace('_', ' ')} must be between 0 and 10,000")
        out[name] = str(value)
    return out


def merged(stored: dict[str, Any] | None) -> dict[str, Price]:
    table = dict(DEFAULTS)
    for model, raw in (stored or {}).items():
        try:
            table[model] = Price(**{name: Decimal(str(raw[name])) for name in FIELDS})
        except (KeyError, InvalidOperation, TypeError):
            continue  # a damaged entry falls back to the default, if there is one
    return table


async def load(session: AsyncSession) -> dict[str, Price]:
    row = await session.get(AppSetting, KEY)
    return merged(row.value if row else None)


def price_for(model: str, table: dict[str, Price]) -> Price | None:
    """The model's price: its own entry, else the longest entry it starts with
    (a dated snapshot such as ``claude-haiku-4-5-20251001`` is its family's price)."""
    if model in table:
        return table[model]
    matches = [name for name in table if model.startswith(name)]
    return table[max(matches, key=len)] if matches else None


def cost(model: str, table: dict[str, Price], *, input_tokens: int, output_tokens: int,
         cache_write_tokens: int = 0, cache_write_1h_tokens: int = 0, cache_read_tokens: int = 0) -> Decimal | None:
    """USD for one call's tokens, or None when the model has no price."""
    price = price_for(model, table)
    if price is None:
        return None
    total = (
        input_tokens * price.input
        + output_tokens * price.output
        + cache_write_tokens * price.cache_write
        + cache_write_1h_tokens * price.cache_write_1h
        + cache_read_tokens * price.cache_read
    )
    return (total / _MILLION).quantize(Decimal("0.00000001"))


def cost_of(row: Any, table: dict[str, Price]) -> Decimal | None:
    return cost(
        row.model, table, input_tokens=row.input_tokens, output_tokens=row.output_tokens,
        cache_write_tokens=row.cache_write_tokens, cache_write_1h_tokens=row.cache_write_1h_tokens,
        cache_read_tokens=row.cache_read_tokens,
    )
