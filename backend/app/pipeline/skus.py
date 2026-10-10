"""A listing's SKU: checked against Etsy's rules, written on every product of a
draft, and kept in the profile's per-size pattern when it has one.

Etsy's rules, in one place (``MAX_LENGTH``, ``FORBIDDEN``): spaces at either end
are trimmed; at most 32 characters; no ``^``, ``$`` or backtick (Etsy refuses an
inventory update carrying them). These were not read from Etsy's OpenAPI document
in the environment that wrote this (it could not reach etsy.com); the draft's
read-back compares the SKUs Etsy kept with those sent, so a rule Etsy applies
that is not here surfaces as a clear error rather than a silent change.

Per-size pattern: when the profile's reference listing gives each variation its
own SKU built from one base (``BR5229-S``, ``BR5229-M``), the new base keeps the
pattern (``CC7001-S``, ``CC7001-M``); otherwise every product gets the base SKU.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

MAX_LENGTH = 32
FORBIDDEN = "^$`"
#: Characters a per-size suffix is joined with ("-S", "_M", ".L", " XL").
SEPARATORS = "-_. "


class SkuInvalid(ValueError):
    pass


def clean(sku: str | None) -> str:
    return (sku or "").strip()


def validate(sku: str | None) -> str:
    """The SKU as it will be written, or :class:`SkuInvalid` saying why not."""
    value = clean(sku)
    if not value:
        raise SkuInvalid("Enter a SKU.")
    if len(value) > MAX_LENGTH:
        raise SkuInvalid(f"A SKU can be at most {MAX_LENGTH} characters on Etsy ({len(value)} now).")
    bad = sorted({c for c in value if c in FORBIDDEN or ord(c) < 32})
    if bad:
        shown = ", ".join("a control character" if ord(c) < 32 else c for c in bad)
        raise SkuInvalid(f"Etsy does not accept {shown} in a SKU.")
    return value


def pattern_base(reference_skus: Sequence[str | None]) -> str | None:
    """The base the reference's per-size SKUs share ("BR5229" of "BR5229-S",
    "BR5229-M"), or None when they do not follow one pattern."""
    skus = [clean(s) for s in reference_skus]
    if len(skus) < 2 or any(not s for s in skus) or len(set(skus)) != len(skus):
        return None
    prefix = os.path.commonprefix(skus)
    # The base ends before a separator every SKU has right after it.
    cut = max((i for i, c in enumerate(prefix) if c in SEPARATORS), default=-1)
    base = prefix[:cut] if cut > 0 else ""
    if not base or any(len(s) <= len(base) + 1 for s in skus):
        return None
    return base


def product_skus(reference_products: Iterable[Mapping[str, Any]], base_sku: str | None) -> dict[int, str]:
    """The SKU for each reference product (by its position), keeping a per-size
    pattern with the new base; every product gets ``base_sku`` when there is none."""
    products = list(reference_products or [])
    base = pattern_base([p.get("sku") for p in products])
    sku = clean(base_sku)
    if not sku:
        return {}
    if base is None:
        return {i: sku for i in range(len(products))}
    return {i: sku + clean(p.get("sku"))[len(base):] for i, p in enumerate(products)}


def variant_skus(current: Sequence[str | None], new_base: str) -> list[str]:
    """The SKUs a draft's products get when its base changes: the per-size
    pattern they follow kept, else the new base on every product."""
    base = pattern_base(current)
    if base is None:
        return [new_base for _ in current]
    return [new_base + clean(s)[len(base):] for s in current]


def check_lengths(skus: Iterable[str]) -> None:
    for sku in skus:
        validate(sku)
