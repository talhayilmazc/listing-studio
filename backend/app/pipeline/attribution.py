"""Tying a statement's orders to listings, and checking the two sources agree.

A statement's order rows carry the order's number (receipt id) and amounts, but
no listing. The sales read (``sale_line``) has, for the same order number, each
line's listing, quantity, price and the shipping the buyer paid. Joined by
receipt id they give every order amount a listing. **Never by title**: the item
names on a statement are truncated and are not read at all.

For an order with several items, what belongs to the order as a whole (the
processing fee, sales tax, the Offsite Ads fee, shipping income...) is split by
each line's share of the items' price: exact to the cent, the remainder going
to the largest line so the parts always add up to the whole.

:func:`reconcile_orders` checks the two sources against each other before
anything is attributed: for each order both know, what the buyer paid less
what was collected for others (the statement) against items plus shipping (the
sales read). It reports the differences; it does not hide or spread them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class LineLike(Protocol):
    listing_id: int
    price_minor: int
    shipping_minor: int
    quantity: int


#: The statement amounts that are the order's own sale, less what was collected
#: for others: what items plus shipping from the sales read should come to.
ORDER_REVENUE = ("sales", "sales_tax", "buyer_fees")


def order_revenue(amounts: dict[str, Any]) -> int:
    return sum(int(amounts.get(c) or 0) for c in ORDER_REVENUE)


def split(amount: int, weights: list[int]) -> list[int]:
    """``amount`` (minor units, any sign) in proportion to ``weights``, exactly:
    the parts always add up to ``amount``. Without weights to go by (all zero)
    it is split evenly."""
    n = len(weights)
    if n == 0:
        return []
    total = sum(weights)
    if total <= 0:
        weights, total = [1] * n, n
    sign = -1 if amount < 0 else 1
    whole = abs(amount)
    parts = [whole * w // total for w in weights]
    left = whole - sum(parts)
    # The cents that do not divide go to the largest shares first.
    for i in sorted(range(n), key=lambda i: (-(whole * weights[i] % total), -weights[i], i))[:left]:
        parts[i] += 1
    return [sign * p for p in parts]


def attribute(amounts: dict[str, int], lines: list[LineLike]) -> dict[int, dict[str, int]]:
    """One order's statement amounts per listing: each category split by the
    lines' share of the items' price. Two lines of the same listing are one."""
    if not lines:
        return {}
    weights = [max(0, line.price_minor) for line in lines]
    out: dict[int, dict[str, int]] = {}
    for category, amount in amounts.items():
        for line, part in zip(lines, split(int(amount or 0), weights), strict=True):
            slot = out.setdefault(line.listing_id, {})
            slot[category] = slot.get(category, 0) + part
    return out


@dataclass
class OrderDifference:
    receipt_id: int
    statement_minor: int  # the sale less sales tax and buyer-paid state fees
    items_minor: int
    shipping_minor: int

    @property
    def difference_minor(self) -> int:
        """Statement minus sales read. Negative: the buyer paid less than items
        plus shipping (a discount is the usual reason)."""
        return self.statement_minor - self.items_minor - self.shipping_minor


@dataclass
class OrderJoin:
    orders: int = 0  # orders with a sale on the statement
    matched: int = 0  # of them, found in the sales read
    exact: int = 0  # matched, and the two sources agree to the cent
    #: Totals over the matched orders.
    statement_minor: int = 0
    items_minor: int = 0
    shipping_minor: int = 0
    #: Sale amount of the orders the sales read does not have.
    unmatched_minor: int = 0
    differing: list[OrderDifference] = field(default_factory=list)

    @property
    def unmatched(self) -> int:
        return self.orders - self.matched

    @property
    def difference_minor(self) -> int:
        return self.statement_minor - self.items_minor - self.shipping_minor


def reconcile_orders(orders: dict[int, dict[str, Any]], lines: dict[int, list[LineLike]]) -> OrderJoin:
    """Statement orders against the sales read, order by order.

    ``orders``: receipt id → the statement's amounts for it (minor units).
    ``lines``: receipt id → its lines from the sales read.
    Only orders whose sale is on the statement are compared.
    """
    out = OrderJoin()
    for receipt_id, amounts in orders.items():
        if not amounts.get("sales"):
            continue  # only fees or a refund fall in this month: nothing to compare
        out.orders += 1
        revenue = order_revenue(amounts)
        mine = lines.get(receipt_id)
        if not mine:
            out.unmatched_minor += revenue
            continue
        out.matched += 1
        items = sum(line.price_minor for line in mine)
        shipping = sum(line.shipping_minor for line in mine)
        out.statement_minor += revenue
        out.items_minor += items
        out.shipping_minor += shipping
        if revenue == items + shipping:
            out.exact += 1
        else:
            out.differing.append(OrderDifference(receipt_id, revenue, items, shipping))
    out.differing.sort(key=lambda d: -abs(d.difference_minor))
    return out
