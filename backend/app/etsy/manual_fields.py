"""Listing settings the seller must make in Shop Manager, because the API cannot.

Some Etsy listing settings are not in Open API v3: createDraftListing and
updateListing do not accept them, and getListing does not return them, so the
app can neither copy them from the reference listing nor check them. Each draft
lists them for the seller, with a link to the draft, before "Publish now", so
they are not first discovered when Etsy refuses to publish.

Etsy's Creativity Standards questions "How does your shop produce this item?"
(made from scratch / assembled from purchased parts / an item my shop alters / a
curated set of purchased goods / a natural material) and "What tools are used to
make this item?" (handheld or hand-guided tools / computerized tools or machines /
an AI generator / none). Checked against Etsy's published OpenAPI document (Open
API v3, spec 3.0.0, fetched 2026-09-24, 2026-09-26, 2026-10-22 and 2026-10-23): no
request or response field of getListing, createDraftListing, updateListing or
updateListingProperty carries either, and the first is not among the category
attributes for the apparel taxonomy either. They are not listed here: the seller
does nothing per listing for them (see MANUAL_FIELDS). It is not "who made it / when was it made" (who_made,
when_made) or production partners: those are in the API, copied from the
reference and verified on read-back (etsy/publisher.py), and recent drafts show
them stored exactly as sent (v7 §E1).

Etsy's developer repository has an open request for exactly these two fields (etsy/open-api
discussion #1630, opened 2026-06-22) with no answer from Etsy as of 2026-10-21. What the live
API really returns for a listing: ``python -m app.cli etsy-raw-listing`` (ops/raw_listing.py).

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


#: Empty since 2026-10-23: Etsy's two How it's made questions are not listed per draft any more.
#: The owner's rule is that the seller does nothing per listing for them (no tick list, nothing that
#: holds up a draft): the profile keeps the answers, says once that Etsy's API cannot set them and
#: where Shop Manager sets them for many listings at once, and every draft sends them the day
#: Etsy adds the field (pipeline/how_made.py, docs/etsy-how-its-made.md).
MANUAL_FIELDS: tuple[ManualField, ...] = ()


def manual_fields_for(template: str | None) -> list[ManualField]:
    """The settings a draft built with this content template still needs by hand."""
    return [f for f in MANUAL_FIELDS if f.templates is None or template in f.templates]
