"""No buyer data is stored, and none reaches the AI provider, a log or an alert.

Etsy's sales answers carry buyer fields. The app reads a handful of fields from
each line (pipeline/sales.py) and drops the rest in memory. This test sends a
shop's sales through the real sales job with buyer fields set to values that
could be found anywhere they leaked to, then looks for them everywhere: every
row of every table, every log record, every alert. It also proves, from the
code itself, that nothing that calls the AI provider can see sales data, and
that no table has anywhere to put a buyer.
"""

from __future__ import annotations

import ast
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import alerts
from app.db.base import Base
from app.db.models import AiCall, SaleLine, SalesDaily
from app.workers import sales as sales_worker
from tests.test_sales import TODAY, EtsyShop, _ctx, _estimated, _patch, _shop, _start, _ts

APP = Path(__file__).resolve().parents[1] / "app"

#: What a buyer field would look like in Etsy's answer. None of these may appear
#: anywhere after the job has run.
BUYER = {
    "buyer_user_id": 918273645,
    "buyer_email": "buyer.jane@buyers.example",
    "name": "Jane Q. Buyerperson",
    "first_line": "742 Buyer Terrace",
    "city": "Buyerville",
    "zip": "BUY3R-ZIP",
    "message_from_buyer": "Please gift wrap for Buyerperson",
    "formatted_address": "Jane Q. Buyerperson, 742 Buyer Terrace, Buyerville",
}
SECRETS = [str(v) for v in BUYER.values()] + ["Buyerperson", "Buyerville", "buyers.example"]


def _sale(n: int) -> dict[str, Any]:
    day = TODAY - timedelta(days=n % 40)
    return {
        "transaction_id": 5_000_000 + n, "receipt_id": 7_000_000 + n // 2, "listing_id": 100 + n % 3, "quantity": 1 + n % 2,
        "price": {"amount": 2500, "divisor": 100, "currency_code": "USD"},
        "shipping_cost": {"amount": 499, "divisor": 100, "currency_code": "USD"},
        "created_timestamp": _ts(day, n % 24), "title": "A listing title",
        "variations": [{"formatted_name": "Personalization", "formatted_value": BUYER["message_from_buyer"]}],
        **BUYER,
    }


async def _everything_stored(sm: async_sessionmaker) -> str:
    """Every value of every row of every table, as one string."""
    out: list[str] = []
    async with sm() as s:
        for table in Base.metadata.sorted_tables:
            for row in (await s.execute(text(f'SELECT * FROM "{table.name}"'))).all():
                out.append(repr(tuple(row)))
    return "\n".join(out)


async def test_buyer_fields_reach_no_table_no_log_and_no_alert(async_sm, monkeypatch, caplog) -> None:
    tenant_id, shop = await _shop(async_sm)
    fake = EtsyShop([_sale(n) for n in range(250)])
    _patch(monkeypatch, tenant_id, fake)
    sent: list[str] = []

    async def record(text_: str) -> bool:
        sent.append(text_)
        return True

    monkeypatch.setattr(alerts, "send", record)
    caplog.set_level(logging.DEBUG)  # everything, from every logger

    ctx = _ctx(async_sm)
    await _estimated(async_sm, ctx, shop)
    await _start(async_sm, shop)
    for _ in range(10):
        if not (await sales_worker.sync_sales(ctx, str(shop))).startswith("reading"):
            break

    async with async_sm() as s:
        lines = (await s.execute(select(SaleLine))).scalars().all()
        days = (await s.execute(select(SalesDaily))).scalars().all()
        assert len(lines) == 250 and sum(d.units for d in days) == sum(1 + n % 2 for n in range(250))
        assert (await s.execute(select(AiCall))).scalars().all() == []  # the sales job calls no model
    stored = await _everything_stored(async_sm)
    logged = "\n".join(record.getMessage() + " " + repr(record.args) for record in caplog.records)
    queued = repr(ctx["enqueue"])
    for secret in SECRETS:
        assert secret not in stored, f"{secret!r} was stored"
        assert secret not in logged, f"{secret!r} was logged"
        assert secret not in "\n".join(sent), f"{secret!r} was sent in an alert"
        assert secret not in queued, f"{secret!r} was put on the queue"


def test_no_table_has_anywhere_to_put_a_buyer() -> None:
    """Sales, ledger and statement tables hold amounts, ids and dates: no name, address, e-mail or message column."""
    words = ("buyer", "customer", "recipient", "address", "first_line", "zip", "postal", "city", "country", "phone", "message", "name", "email")
    for table in ("sale_line", "sales_daily", "ledger_daily", "statement_import", "statement_order", "statement_listing_fee",
                  "ads_daily", "ad_charge", "listing_stat_daily", "sales_sync", "ledger_sync"):
        columns = set(Base.metadata.tables[table].c.keys())
        assert not [c for c in columns if any(w in c.lower() for w in words)], (table, columns)
    assert set(Base.metadata.tables["sale_line"].c.keys()) == {
        "connection_id", "transaction_id", "tenant_id", "receipt_id", "listing_id", "day", "quantity", "price_minor", "shipping_minor", "currency"}
    assert set(Base.metadata.tables["ledger_daily"].c.keys()) == {
        "connection_id", "day", "ledger_type", "tenant_id", "amount_minor", "entries", "currency"}
    assert set(Base.metadata.tables["statement_order"].c.keys()) == {"connection_id", "month", "receipt_id", "tenant_id", "day", "amounts"}


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found |= {f"{node.module}.{alias.name}" for alias in node.names}
        elif isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
    return found


def test_nothing_that_calls_the_ai_provider_can_see_sales_ledger_or_statement_data() -> None:
    """The model is called from four places (vision, content, the size-chart check and the
    jobs that use them). None of them imports the sales, ledger or statement code or models,
    and the receipts endpoint, which returns buyer names and addresses, is not called at all."""
    money = ("app.pipeline.sales", "app.pipeline.ledger", "app.pipeline.statement", "app.pipeline.ads_report", "app.pipeline.attribution",
             "app.pipeline.finance", "app.pipeline.pnl", "app.workers.sales", "app.workers.ledger", "app.api.imports", "app.api.analytics", "app.api.pnl")
    tables = ("SaleLine", "SalesDaily", "LedgerDaily", "StatementImport", "StatementOrder", "StatementListingFee", "AdsDaily", "AdCharge")
    callers = [p for p in APP.rglob("*.py") if "complete_json" in p.read_text(encoding="utf-8") or "client_for(" in p.read_text(encoding="utf-8")]
    assert {p.name for p in callers} >= {"llm.py", "vision.py", "content.py", "imageclass.py", "batches.py", "replace.py"}
    for path in callers:
        imported = _imports(path)
        assert not [m for m in imported if m.startswith(money)], f"{path.name} imports money code: {sorted(m for m in imported if m.startswith(money))}"
        assert not [t for t in tables if f"app.db.models.{t}" in imported], f"{path.name} imports a sales table"
    # And the other way round: the code that reads sales never builds a model client.
    for name in ("workers/sales.py", "workers/ledger.py", "pipeline/sales.py", "pipeline/ledger.py", "pipeline/statement.py", "api/imports.py"):
        source = (APP / name).read_text(encoding="utf-8")
        assert "client_for(" not in source and "complete_json" not in source and "app.pipeline.llm" not in _imports(APP / name), name
    # getShopReceipts is never requested (it carries the buyer's name and address).
    api = (APP / "etsy" / "api.py").read_text(encoding="utf-8")
    assert "/receipts\"" not in api and "def get_shop_receipts(" not in api
