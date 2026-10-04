"""Local only: the real statement and Ads report against their known totals.

The files are a live shop's and are never committed (``docs/samples/`` is
git-ignored). Neither are the totals: this test reads what to expect from
``expected.json`` in the same folder, so no real figure is written in the
repository. Without the folder it skips.

Where the files are looked for: ``ETSY_SAMPLES_DIR``, else ``docs/samples`` at
the repository root. The API container does not see ``docs/``; run it with the
folder mounted:

    docker compose run --rm --no-deps -v "$PWD/docs/samples:/samples:ro" \\
        -e ETSY_SAMPLES_DIR=/samples api python -m pytest -q tests/test_statement_samples.py

``expected.json``:
    {"statement_file": "<file>.csv", "ads_file": "<file>.csv",
     "totals": {"<category>": "<amount>", ...}, "net_total": "...", "revenue": "...",
     "sale_orders": n, "refund_orders": n, "listing_fees": n, "listings_with_fees": n,
     "deposits": n,
     "ads": {"spend": "...", "revenue": "...", "orders": n, "clicks": n, "views": n},
     "reconciliation": {"charged": "...", "billed_later": {"<day>": "..."},
                        "billed_from_before": {"<day>": "..."},
                        "billed_differently": {"<day>": ["<reported>", "<charged>"]}}}
"""

from __future__ import annotations

import json
import os
from decimal import Decimal as D
from pathlib import Path

import pytest

from app.pipeline import ads_report, statement


def _folder() -> Path | None:
    candidates = [os.environ.get("ETSY_SAMPLES_DIR"), Path(__file__).resolve().parents[2] / "docs" / "samples"]
    for candidate in candidates:
        if candidate and (Path(candidate) / "expected.json").is_file():
            return Path(candidate)
    return None


FOLDER = _folder()
pytestmark = pytest.mark.skipif(FOLDER is None, reason="the real sample files are not on this machine")


@pytest.fixture(scope="module")
def expected() -> dict:
    return json.loads((FOLDER / "expected.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def parsed(expected) -> statement.Statement:
    return statement.parse((FOLDER / expected["statement_file"]).read_bytes())


def test_every_category_total_matches_the_answer_key(parsed, expected) -> None:
    assert {k: str(v) for k, v in parsed.totals.items()} == expected["totals"]
    assert parsed.net_total == D(expected["net_total"]) == sum(parsed.totals.values())
    assert parsed.revenue == D(expected["revenue"])
    assert parsed.unrecognised == [] and parsed.notes == []


def test_orders_listings_and_deposits_match(parsed, expected) -> None:
    assert parsed.sale_orders == expected["sale_orders"]
    assert sum(1 for o in parsed.orders.values() if "refunds" in o.amounts) == expected["refund_orders"]
    assert sum(f[0] for f in parsed.listing_fees.values()) == expected["listing_fees"]
    assert sum(1 for f in parsed.listing_fees.values() if f[0]) == expected["listings_with_fees"]
    assert len(parsed.deposits) == expected["deposits"]
    # Every order with a sale has exactly what attribution needs.
    sold = [o for o in parsed.orders.values() if "sales" in o.amounts]
    assert all("processing_fee" in o.amounts and "transaction_fee_items" in o.amounts and o.day is not None for o in sold)


def test_the_ads_report_and_its_reconciliation_with_the_statement(parsed, expected) -> None:
    report = ads_report.parse((FOLDER / expected["ads_file"]).read_bytes())
    want = expected["ads"]
    assert (str(report.spend), str(report.revenue), report.orders, report.clicks, report.views) == (
        want["spend"], want["revenue"], want["orders"], want["clicks"], want["views"],
    )
    rec = ads_report.reconcile(parsed.ad_charges, report)
    want = expected["reconciliation"]
    assert rec.charged_total == D(want["charged"]) == -parsed.total("etsy_ads")
    assert {d.day.isoformat(): str(d.reported) for d in rec.billed_later} == want["billed_later"]
    assert {d.day.isoformat(): str(d.charged) for d in rec.billed_from_before} == want["billed_from_before"]
    assert {d.day.isoformat(): [str(d.reported), str(d.charged)] for d in rec.billed_differently} == want["billed_differently"]
    assert rec.exact
    assert all((c.posted - c.click_day).days == 1 for c in parsed.ad_charges)
