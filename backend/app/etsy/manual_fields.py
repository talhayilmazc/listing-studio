"""Listing settings the seller must make in Shop Manager, because the API cannot.

Some Etsy listing settings are not in Open API v3: createDraftListing and
updateListing do not accept them, and getListing does not return them, so the
app can neither copy them from the reference listing nor check them. Each draft
lists them for the seller, with a link to the draft, before "Publish now", so
they are not first discovered when Etsy refuses to publish.

The first is Etsy's Creativity Standards question, "How does your shop produce
this item?". Checked against Etsy's published OpenAPI document (Open API v3,
spec 3.0.0, fetched 2026-09-24 and again 2026-09-26): no request or response
field carries it, and it is not among the category attributes for the apparel
taxonomy either. It is not "who made it / when was it made" (who_made,
when_made) or production partners: those are in the API, copied from the
reference and verified on read-back (etsy/publisher.py), and recent drafts show
them stored exactly as sent (v7 §E1).

To add another, append a ManualField below. If Etsy later exposes one of these
through the API, copy it from the reference like every other setting instead,
verify it on read-back, and delete its entry here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ManualField:
    key: str
    #: The setting as Etsy labels it in Shop Manager.
    label: str
    #: What the seller has to do, in one or two sentences.
    detail: str
    #: Content templates it applies to; None = every listing.
    templates: frozenset[str] | None = None


MANUAL_FIELDS: tuple[ManualField, ...] = (
    ManualField(
        key="creativity_production",
        label="How does your shop produce this item?",
        detail=(
            "Etsy's Creativity Standards question: made from scratch, assembled from "
            "purchased parts, altered by your shop, a curated set of purchased goods, or a "
            "natural material. Who made it, when, and any production partner are copied from "
            "your reference and checked; this question is separate, and Etsy's API cannot set "
            "it, so choose it on the draft in Shop Manager."
        ),
    ),
)


def manual_fields_for(template: str | None) -> list[ManualField]:
    """The settings a draft built with this content template still needs by hand."""
    return [f for f in MANUAL_FIELDS if f.templates is None or template in f.templates]
