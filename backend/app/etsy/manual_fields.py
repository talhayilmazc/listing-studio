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
draft lists them, and the profile card says so in one line (``PROFILE_NOTE``). It is not "who made it / when was it made" (who_made,
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
    ManualField(
        key="creativity_tools",
        label="What tools are used to make this item?",
        detail=(
            "Etsy's Creativity Standards question: handheld or hand-guided tools, computerized tools "
            "or machines, an AI generator, or none. Etsy's API cannot read or set it, so choose it on "
            "the draft in Shop Manager, as on your reference listing."
        ),
    ),
)

#: The one line a profile card shows about them (they belong to the item, not to a shop).
PROFILE_NOTE = (
    "Etsy's API cannot read or set \"How does your shop produce this item?\" or \"What tools are used to "
    "make this item?\": set them on each draft in Shop Manager, as on the reference listing. "
    "Each draft lists them to tick."
)


def manual_fields_for(template: str | None) -> list[ManualField]:
    """The settings a draft built with this content template still needs by hand."""
    return [f for f in MANUAL_FIELDS if f.templates is None or template in f.templates]
