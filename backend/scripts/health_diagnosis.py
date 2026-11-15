"""Listing health version 2 on the synthetic shop (app/pipeline/health_fixture.py): the bucket of every listing.

    docker compose run --rm -w /app -e PYTHONPATH=/app --entrypoint python tests scripts/health_diagnosis.py

Synthetic data only; for a real account use ``python -m app.cli listing-health-report <e-mail>`` (read only).
"""

from collections import Counter
from datetime import datetime, timezone

from app.pipeline import health_fixture as fx
from app.pipeline import listing_fixes as F
from app.pipeline import listing_health as H

now = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
required = [fx.REQUIRED]
buckets: Counter = Counter()
for item in fx.shop(now):
    p = item.payload
    text = F.text_of(p)
    verdict = H.classify(
        p, recent_units=fx.sales_units(item, within_days=60), past_units=fx.sales_units(item), sales_known=True,
        properties=p.get("_properties"), required=required, reference_attributes=fx.REFERENCE_ATTRIBUTES,
        tags_to_add=F.added_tags(text["title"], text["tags"], fx.REFERENCE_TAGS), now=now,
    )
    bucket = verdict.bucket if verdict else "not listed"
    buckets[bucket] += 1
    problems = "; ".join(f"{q.id} -> {'/'.join(q.fixes)}" for q in (verdict.problems if verdict else []))
    print(f"{item.listing_id}  {bucket:16} {item.why}  [{problems}]")
print()
for b in (*H.BUCKETS, "not listed"):
    print(f"{b}: {buckets[b]}")
