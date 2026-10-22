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
API v3, spec 3.0.0, fetched 2026-09-24, 2026-09-26 and again 2026-10-22, when the
owner asked for them): no request or response field of getListing,
createDraftListing or updateListing carries either, and the first is not among
the category attributes for the apparel taxonomy either. Nothing is guessed: each
draft lists them with the profile's own answers to tick (pipeline/how_made.py). It is not "who made it / when was it made" (who_made,
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


MANUAL_FIELDS: tuple[ManualField, ...] = (
    ManualField(
        key="creativity_production",
        label="How does your shop produce this item?",
        # Replaced on each draft by the profile's own answer to tick (api/content.manual_steps).
        detail="Etsy's API cannot set it: tick your profile's answer on the listing in Etsy.",
    ),
    ManualField(
        key="creativity_tools",
        label="What tools are used to make this item?",
        detail="Etsy's API cannot set it: tick your profile's answer on the listing in Etsy.",
    ),
)

# The profile card says the profile's own answers instead ("How it's made: …", pipeline/how_made.py).


def manual_fields_for(template: str | None) -> list[ManualField]:
    """The settings a draft built with this content template still needs by hand."""
    return [f for f in MANUAL_FIELDS if f.templates is None or template in f.templates]
