"""Etsy's monthly statement, read into exact totals.

The file is what a seller downloads from Shop Manager → Finances → Monthly
statements → Download CSV. It is the authority for a month's money: every
sale, fee, tax, ad charge, refund and bank transfer Etsy posted in that month.

It is parsed in memory and only two things are kept from it:
  * a total per category (below), and
  * per order (receipt id) and per listing (listing id), the amounts needed to
    attribute fees later.
The file itself is never stored. It holds no buyer names or addresses; item
titles appear on transaction-fee rows and are not kept (they are truncated and
must never be used to guess a listing).

How the file is laid out (verified against a real September statement):
  * columns Date, Type, Title, Info, Currency, Amount, "Fees & Taxes", Net,
    "Tax Details"; UTF-8 with a byte-order mark; dates like "September 30, 2026"
  * money like "$1,234.56" and "-$0.20"; "--" is zero. A sale's amount is in
    Amount, a fee's in "Fees & Taxes"; Net is their sum and is what is added up
  * a sale is ``Payment for Order #<receipt id>`` with an empty Info; the fees
    and taxes of an order carry ``Order #<receipt id>`` in Info; a listing fee
    carries ``Listing #<listing id>``
  * an Etsy Ads row is ``Bill for click-throughs to your shop on <Mon D, YYYY>``:
    the charge is for that day's clicks and is posted the day after
  * a Deposit is a transfer to the seller's bank. Its amount is only in the
    title, its Net is zero, and it is neither income nor cost

Every row lands in exactly one category, so the categories add up to the
file's Net total to the cent. A row this module does not recognise is not
dropped: it is counted under its type's "other" category and listed.
"""

from __future__ import annotations

import csv
import html
import io
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

COLUMNS = ("Date", "Type", "Title", "Info", "Currency", "Amount", "Fees & Taxes", "Net", "Tax Details")

#: Category → (label, group). The order is the order a P&L lists them in.
#: Groups: "sales" (what buyers paid), "pass_through" (collected for someone
#: else: never revenue, never cost), "refunds", "etsy_fees", "ads", "shipping",
#: "other" (rows not recognised; counted, and listed for the seller to see).
CATEGORIES: dict[str, tuple[str, str]] = {
    "sales": ("Sales (what buyers paid, sales tax included)", "sales"),
    "sales_tax": ("Sales tax paid by buyer", "pass_through"),
    "sales_tax_refund": ("Refund to buyer for sales tax", "pass_through"),
    "buyer_fees": ("Fees paid by buyer for the state (e.g. Colorado Retail Delivery Fee)", "pass_through"),
    "refunds": ("Refunds", "refunds"),
    "transaction_fee_items": ("Transaction fee on items", "etsy_fees"),
    "transaction_fee_shipping": ("Transaction fee on shipping", "etsy_fees"),
    "processing_fee": ("Processing fee", "etsy_fees"),
    "listing_fee": ("Listing fee", "etsy_fees"),
    "fee_credits": ("Fee credits", "etsy_fees"),
    "transaction_tax": ("Tax on Etsy's fees (Tax: Transaction)", "etsy_fees"),
    "transaction_tax_credit": ("Tax on Etsy's fees, credited", "etsy_fees"),
    "other_fees": ("Other fees", "etsy_fees"),
    "other_tax": ("Other tax", "etsy_fees"),
    "etsy_ads": ("Etsy Ads", "ads"),
    "offsite_ads": ("Offsite Ads fees", "ads"),
    "offsite_ads_credit": ("Offsite Ads credits", "ads"),
    "other_marketing": ("Other marketing", "ads"),
    "shipping": ("Shipping labels and adjustments", "shipping"),
    "unrecognised": ("Not recognised", "other"),
}
#: Categories that mean the parser met a title it has no rule for.
FALLBACKS = frozenset({"other_fees", "other_tax", "other_marketing", "unrecognised"})

_MONTHS = {name: i + 1 for i, name in enumerate(
    ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december")
)}
_DATE = re.compile(r"^\s*([A-Za-z]+)\.?\s+(\d{1,2}),\s*(\d{4})\s*$")
_MONEY = re.compile(r"^(-?)[^\d\-]*(\d[\d,]*(?:\.\d+)?)$")
_ORDER = re.compile(r"Order #(\d+)")
_LISTING = re.compile(r"Listing #(\d+)")
_CLICKS_ON = re.compile(r"click-throughs to your shop on (.+)$")
_DEPOSIT = re.compile(r"^(\S+) sent to your bank account")


class StatementError(ValueError):
    """The file is not a statement this module can read (message is safe to show)."""


def parse_day(text: str) -> date:
    """ "September 30, 2026" or "Sep 1, 2026" → date. English month names, whatever the server's locale."""
    m = _DATE.match(text or "")
    if m:
        key = m.group(1).lower()
        month = _MONTHS.get(key) or next((n for name, n in _MONTHS.items() if len(key) >= 3 and name.startswith(key[:3])), None)
        if month:
            try:
                return date(int(m.group(3)), month, int(m.group(2)))
            except ValueError:
                pass
    raise StatementError(f"a date could not be read: {text!r}")


def parse_money(text: str) -> Decimal:
    """ "$1,234.56" → 1234.56, "-$0.20" → -0.20, "--" (and empty) → 0."""
    value = (text or "").strip().replace("−", "-")
    if value in ("", "--"):
        return Decimal("0")
    m = _MONEY.match(value)
    if not m:
        raise StatementError(f"an amount could not be read: {text!r}")
    try:
        amount = Decimal(m.group(2).replace(",", ""))
    except InvalidOperation as exc:
        raise StatementError(f"an amount could not be read: {text!r}") from exc
    return -amount if m.group(1) else amount


@dataclass
class Order:
    """What the statement says about one order (receipt), for attribution.
    No buyer data: the statement carries none, and none is added."""

    receipt_id: int
    #: The day the sale was posted; None when only its fees fall in this month.
    day: date | None = None
    amounts: dict[str, Decimal] = field(default_factory=dict)  # category → net

    def add(self, category: str, net: Decimal) -> None:
        self.amounts[category] = self.amounts.get(category, Decimal("0")) + net


@dataclass
class AdCharge:
    click_day: date  # the day whose clicks are billed
    posted: date  # the day the charge appears on the statement
    amount: Decimal  # as a cost: positive


@dataclass
class Unrecognised:
    type: str
    title: str
    category: str
    net: Decimal


@dataclass
class Statement:
    rows: int = 0
    first_day: date | None = None
    last_day: date | None = None
    currency: str | None = None
    #: The Net column added up, every row. The categories add up to exactly this.
    net_total: Decimal = Decimal("0")
    totals: dict[str, Decimal] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    #: Fee credits by what they credit: "listing", "transaction_items",
    #: "transaction_shipping", "processing", "other". They add up to ``fee_credits``.
    credits: dict[str, Decimal] = field(default_factory=dict)
    orders: dict[int, Order] = field(default_factory=dict)
    #: listing id → (fees charged, their total, credits' total)
    listing_fees: dict[int, list] = field(default_factory=dict)
    ad_charges: list[AdCharge] = field(default_factory=list)
    #: Transfers to the bank: (day, amount). Not income, not cost.
    deposits: list[tuple[date, Decimal]] = field(default_factory=list)
    unrecognised: list[Unrecognised] = field(default_factory=list)
    #: Things the reader should know (a row whose Amount + Fees is not its Net, ...).
    notes: list[str] = field(default_factory=list)

    def total(self, *categories: str) -> Decimal:
        return sum((self.totals.get(c, Decimal("0")) for c in categories), Decimal("0"))

    def group(self, name: str) -> Decimal:
        return self.total(*(c for c, (_, g) in CATEGORIES.items() if g == name))

    @property
    def deposits_total(self) -> Decimal:
        return sum((amount for _, amount in self.deposits), Decimal("0"))

    @property
    def revenue(self) -> Decimal:
        """Sales without what was collected for someone else (sales tax, state
        fees paid by the buyer), before refunds. Never the raw sales figure."""
        return self.total("sales", "sales_tax", "buyer_fees")

    @property
    def refunds_net(self) -> Decimal:
        """Refunds, less the sales tax that went back with them."""
        return self.total("refunds", "sales_tax_refund")

    @property
    def sale_orders(self) -> int:
        return self.counts.get("sales", 0)


def _classify(kind: str, title: str) -> str:
    """The category of a row, from its Type and the start of its Title. Never
    from an item's name: the text after "Transaction fee:" is a truncated title."""
    t = title.strip()
    low = t.lower()
    if kind == "Sale":
        return "sales" if low.startswith("payment for order") else "unrecognised"
    if kind == "Refund":
        return "refunds"
    if kind == "Buyer Fee":
        return "buyer_fees"
    if kind == "Shipping":
        return "shipping"
    if kind == "Tax":
        if low.startswith("sales tax paid by buyer"):
            return "sales_tax"
        if low.startswith("refund to buyer for sales tax"):
            return "sales_tax_refund"
        if low.startswith("tax: transaction credit"):
            return "transaction_tax_credit"
        if low.startswith("tax: transaction"):
            return "transaction_tax"
        return "other_tax"
    if kind == "Fee":
        if low.startswith("credit for"):
            return "fee_credits"
        if low == "listing fee":
            return "listing_fee"
        if low == "processing fee":
            return "processing_fee"
        if low == "transaction fee: shipping":  # exactly: an item's name may start with "Shipping"
            return "transaction_fee_shipping"
        if low.startswith("transaction fee:"):
            return "transaction_fee_items"
        return "other_fees"
    if kind == "Marketing":
        if low == "etsy ads":
            return "etsy_ads"
        if low.startswith("credit for offsite ads"):
            return "offsite_ads_credit"
        if "offsite ads" in low:
            return "offsite_ads"
        return "other_marketing"
    return "unrecognised"


def credit_kind(title: str) -> str:
    """Which fee a "Credit for ..." row gives back, from the fixed start of its title."""
    low = title.strip().lower()
    if low.startswith("credit for listing fee"):
        return "listing"
    if low.startswith("credit for processing fee"):
        return "processing"
    if low == "credit for transaction fee on shipping":
        return "transaction_shipping"
    if low.startswith("credit for transaction fee on"):
        return "transaction_items"
    return "other"


def parse(data: bytes | str) -> Statement:
    """Read a statement CSV. Raises StatementError when it is not one."""
    text = data.decode("utf-8-sig", errors="strict") if isinstance(data, bytes) else data.lstrip("﻿")
    reader = csv.reader(io.StringIO(text))
    try:
        header = [h.strip() for h in next(reader)]
    except StopIteration:
        raise StatementError("the file is empty") from None
    missing = [c for c in COLUMNS[:8] if c not in header]
    if missing:
        raise StatementError(
            "this is not an Etsy monthly statement: the column"
            + ("s " if len(missing) > 1 else " ")
            + ", ".join(repr(c) for c in missing)
            + (" are" if len(missing) > 1 else " is")
            + " missing"
        )
    at = {name: header.index(name) for name in COLUMNS if name in header}

    out = Statement()
    currencies: set[str] = set()
    for line, row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in row):
            continue
        if len(row) < len(header):
            row = row + [""] * (len(header) - len(row))
        kind = row[at["Type"]].strip()
        title = html.unescape(row[at["Title"]]).strip()
        info = html.unescape(row[at["Info"]]).strip()
        try:
            day = parse_day(row[at["Date"]])
            amount = parse_money(row[at["Amount"]])
            fees = parse_money(row[at["Fees & Taxes"]])
            net = parse_money(row[at["Net"]])
        except StatementError as exc:
            raise StatementError(f"line {line}: {exc}") from None
        out.rows += 1
        out.first_day = day if out.first_day is None else min(out.first_day, day)
        out.last_day = day if out.last_day is None else max(out.last_day, day)
        if row[at["Currency"]].strip():
            currencies.add(row[at["Currency"]].strip())
        if amount + fees != net:
            out.notes.append(f"line {line}: Amount plus Fees & Taxes is not the row's Net; Net is what was counted")

        if kind == "Deposit":
            # Money moved to the bank: not part of the month's result. Its
            # amount is written only in the title.
            m = _DEPOSIT.match(title)
            try:
                out.deposits.append((day, parse_money(m.group(1)) if m else Decimal("0")))
            except StatementError:
                out.deposits.append((day, Decimal("0")))
                out.notes.append(f"line {line}: a deposit's amount could not be read from its title")
            out.net_total += net  # zero on every statement seen; counted if it ever is not
            if net:
                out.totals["unrecognised"] = out.totals.get("unrecognised", Decimal("0")) + net
                out.counts["unrecognised"] = out.counts.get("unrecognised", 0) + 1
                out.unrecognised.append(Unrecognised(kind, "deposit with a net amount", "unrecognised", net))
            continue

        category = _classify(kind, title)
        out.net_total += net
        out.totals[category] = out.totals.get(category, Decimal("0")) + net
        out.counts[category] = out.counts.get(category, 0) + 1
        if category == "fee_credits":
            kind_of_credit = credit_kind(title)
            out.credits[kind_of_credit] = out.credits.get(kind_of_credit, Decimal("0")) + net
        if category in FALLBACKS:
            # The start of the title says what it is; an item's name is cut off.
            out.unrecognised.append(Unrecognised(kind, title.split(":")[0][:60], category, net))

        order = _ORDER.search(title if kind in ("Sale", "Refund") else info)
        if order:
            entry = out.orders.setdefault(int(order.group(1)), Order(int(order.group(1))))
            entry.add(category, net)
            if category == "sales":
                entry.day = day
        listing = _LISTING.search(info)
        if listing and category in ("listing_fee", "fee_credits"):
            slot = out.listing_fees.setdefault(int(listing.group(1)), [0, Decimal("0"), Decimal("0")])
            if category == "listing_fee":
                slot[0] += 1
                slot[1] += net
            else:
                slot[2] += net
        if category == "etsy_ads":
            m = _CLICKS_ON.search(info)
            try:
                click_day = parse_day(m.group(1)) if m else None
            except StatementError:
                click_day = None
            if click_day is None:
                out.notes.append(f"line {line}: an Etsy Ads charge does not say which day's clicks it is for")
                click_day = day
            out.ad_charges.append(AdCharge(click_day=click_day, posted=day, amount=-net))

    if out.rows == 0:
        raise StatementError("the statement has no rows")
    if len(currencies) > 1:
        raise StatementError("the statement mixes currencies (" + ", ".join(sorted(currencies)) + "); one statement has one currency")
    out.currency = next(iter(currencies), None)
    return out
