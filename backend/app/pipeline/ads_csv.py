"""Reading an Etsy Ads CSV the seller exported from Shop Manager (v7 §C1).

The Open API has no Ads data, so the seller downloads the report themselves
(Shop Manager → Marketing → Etsy Ads) and uploads it here; no browser
automation reads it for them (ToU §9). The file is parsed in memory and never
stored: only the matched rows' spend, orders and revenue per listing and
period become ``ad_spend`` rows.

Column names differ between exports and languages, so the seller maps them on
a screen; :func:`guess_mapping` only pre-fills that screen. Rows are matched to
the seller's OWN listings only: by listing id when the export has one,
otherwise by exact title.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 20_000
PREVIEW_ROWS = 5

#: Mapping fields: which CSV column holds each value. ``listing_id`` or ``title``
#: is required, and ``spend``; ``date`` or a period entered on the screen.
FIELDS = ("listing_id", "title", "date", "spend", "orders", "revenue", "views")

_GUESSES: dict[str, tuple[str, ...]] = {
    "listing_id": ("listing id", "listing_id", "listingid", "listing #", "id"),
    "title": ("listing title", "title", "listing", "ad", "product"),
    "date": ("date", "day"),
    "spend": ("spend", "ad spend", "cost", "spent"),
    "orders": ("orders", "ad orders", "sales"),
    "revenue": ("revenue", "ad revenue", "sales revenue"),
    "views": ("views", "ad views", "impressions"),
}


class CsvError(ValueError):
    """The file can't be read as a CSV report (message is safe to show)."""


@dataclass
class Table:
    headers: list[str]
    rows: list[list[str]]


def read_table(data: bytes) -> Table:
    if len(data) > MAX_BYTES:
        raise CsvError("the file is larger than 5 MB")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - cp1252 decodes nearly anything
        raise CsvError("the file is not text")
    sample = text[:4096]
    try:
        dialect: Any = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        raise CsvError("the file is empty")
    headers = [h.strip() for h in rows[0]]
    if len(rows) - 1 > MAX_ROWS:
        raise CsvError(f"the file has more than {MAX_ROWS} rows")
    return Table(headers=headers, rows=rows[1:])


def guess_mapping(headers: list[str]) -> dict[str, str | None]:
    """Pre-fill the mapping screen from header names; each column used once."""
    norm = {h: re.sub(r"[^a-z0-9#_ ]", "", h.casefold()).strip() for h in headers}
    used: set[str] = set()
    out: dict[str, str | None] = {}
    for key in ("listing_id", "date", "spend", "revenue", "orders", "views", "title"):
        pick = None
        for guess in _GUESSES[key]:
            pick = next((h for h in headers if h not in used and norm[h] == guess), None)
            if pick:
                break
        if pick is None and key != "listing_id":
            pick = next(
                (h for h in headers if h not in used and any(g in norm[h] for g in _GUESSES[key] if len(g) > 3)),
                None,
            )
        if pick:
            used.add(pick)
        out[key] = pick
    return {k: out.get(k) for k in FIELDS}


def parse_money(text: str) -> int | None:
    """"$1,234.56", "1.234,56 €", "(12.00)" → minor units; blank → None."""
    s = text.strip()
    if not s or s in {"-", "—"}:
        return None
    negative = s.startswith("(") and s.endswith(")") or "-" in s
    s = re.sub(r"[^0-9.,]", "", s)
    if not s:
        raise ValueError("not an amount")
    if "," in s and "." in s:
        # The later separator is the decimal one.
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        head, _, tail = s.rpartition(",")
        s = f"{head.replace(',', '')}.{tail}" if len(tail) in (1, 2) else s.replace(",", "")
    try:
        value = Decimal(s)
    except InvalidOperation:
        raise ValueError("not an amount") from None
    minor = int((value * 100).to_integral_value())
    return -minor if negative else minor


def parse_count(text: str) -> int:
    s = re.sub(r"[^0-9.,]", "", text.strip())
    if not s:
        return 0
    return int(Decimal(s.replace(",", "")))


_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%d.%m.%Y", "%b %d, %Y", "%d %b %Y", "%Y/%m/%d")


def parse_date(text: str) -> date:
    s = text.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError("not a date")


def _title_key(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().casefold()


@dataclass
class ParsedRow:
    listing_id: int
    period_start: date
    period_end: date
    spend_minor: int
    ad_orders: int
    ad_revenue_minor: int
    ad_views: int = 0


@dataclass
class Unmatched:
    line: int  # 1-based, counting the header as line 1
    label: str
    why: str


@dataclass
class ParseResult:
    rows: list[ParsedRow] = field(default_factory=list)
    unmatched: list[Unmatched] = field(default_factory=list)
    skipped: int = 0  # blank or zero-spend rows, and totals rows


def parse_rows(
    table: Table,
    mapping: dict[str, str | None],
    own_ids: set[int],
    own_titles: dict[str, int],
    period: tuple[date, date] | None,
) -> ParseResult:
    """Rows matched to the seller's own listings, and the rows that weren't.

    ``own_ids`` and ``own_titles`` (normalised title → id) come from the
    seller's own shop only, so a row naming anyone else's listing never
    matches.
    """
    col = {k: (table.headers.index(v) if v in table.headers else None) for k, v in mapping.items() if v}
    if col.get("spend") is None:
        raise CsvError("choose the column that holds the ad spend")
    if col.get("listing_id") is None and col.get("title") is None:
        raise CsvError("choose the column that holds the listing id or the listing title")
    if col.get("date") is None and period is None:
        raise CsvError("choose the date column, or enter the period the report covers")

    def cell(row: list[str], key: str) -> str:
        i = col.get(key)
        return row[i] if i is not None and i < len(row) else ""

    out = ParseResult()
    for n, row in enumerate(table.rows, start=2):
        raw_id, raw_title = cell(row, "listing_id").strip(), cell(row, "title").strip()
        label = raw_title or raw_id or f"line {n}"
        if raw_title.casefold() in {"total", "totals", "all listings"} or raw_id.casefold() in {"total", "totals"}:
            out.skipped += 1
            continue
        try:
            spend = parse_money(cell(row, "spend"))
            orders = parse_count(cell(row, "orders")) if "orders" in col else 0
            revenue = (parse_money(cell(row, "revenue")) or 0) if "revenue" in col else 0
            views = parse_count(cell(row, "views")) if "views" in col else 0
        except (ValueError, InvalidOperation):
            out.unmatched.append(Unmatched(n, label[:120], "an amount could not be read"))
            continue
        if not spend and not orders and not revenue and not views:
            out.skipped += 1
            continue
        if "date" in col:
            try:
                start = end = parse_date(cell(row, "date"))
            except ValueError:
                out.unmatched.append(Unmatched(n, label[:120], "the date could not be read"))
                continue
        else:
            assert period is not None
            start, end = period

        listing_id: int | None = None
        digits = re.sub(r"\D", "", raw_id)
        if digits and int(digits) in own_ids:
            listing_id = int(digits)
        elif raw_title and _title_key(raw_title) in own_titles:
            listing_id = own_titles[_title_key(raw_title)]
        if listing_id is None:
            why = "not one of your shop's listings" if (digits or raw_title) else "no listing id or title"
            out.unmatched.append(Unmatched(n, label[:120], why))
            continue
        out.rows.append(ParsedRow(listing_id, start, end, spend or 0, orders, revenue, views))
    return out


def own_title_index(titles: dict[int, str]) -> dict[str, int]:
    """Normalised title → listing id; a title two listings share matches neither."""
    index: dict[str, int] = {}
    dup: set[str] = set()
    for listing_id, title in titles.items():
        key = _title_key(title)
        if not key:
            continue
        if key in index:
            dup.add(key)
        index[key] = listing_id
    for key in dup:
        index.pop(key, None)
    return index
