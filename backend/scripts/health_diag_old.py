"""One-off: the OLD Listing health score on the synthetic shop (docs/listing-health.md, step 1)."""
from collections import Counter
from datetime import datetime, timezone

from app.pipeline import health_fixture as fx
from app.pipeline import listing_health as H

now = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
items = [i for i in fx.shop(now) if i.payload["state"] == "active"]
selling = [i for i in items if fx.sales_units(i, within_days=60) > 0]
rest = [i for i in items if i not in selling]
fired_s, fired_r, kinds = Counter(), Counter(), {}
scores_s, scores_r = [], []
for group, fired, scores in ((selling, fired_s, scores_s), (rest, fired_r, scores_r)):
    for i in group:
        h = H.score(i.payload, properties=i.payload.get("_properties"), reference_attributes=fx.REFERENCE_ATTRIBUTES, short_style=True)
        scores.append(h.score)
        for c in h.checks:
            kinds[c.id] = c.kind
            if c.verdict in (H.FAIL, H.WARN):
                fired[c.id] += 1
print(f"active {len(items)} selling {len(selling)} not {len(rest)}")
print(f"score selling avg {sum(scores_s)/len(scores_s):.0f} min {min(scores_s)} max {max(scores_s)}; not selling avg {sum(scores_r)/len(scores_r):.0f}")
print("| rule | whose | fires on selling | fires on not selling |")
for rid in sorted(kinds, key=lambda r: -fired_s[r]):
    print(f"| {rid} | {kinds[rid]} | {fired_s[rid]} of {len(selling)} ({100*fired_s[rid]//len(selling)}%) | {fired_r[rid]} of {len(rest)} |")
