"""Etsy's Creativity Standards, as far as a draft's settings go.

A draft copies "who made it", "when it was made", whether it is a supply, and
the production partners from the seller's own reference listing
(etsy/publisher.py), and reads them back after the draft is made. So what the
draft says is what the reference listing says; this module only notices the one
case worth a warning before a draft is made.

**A print-on-demand profile with no production partner.** Etsy asks a seller
who uses a production partner (a print-on-demand provider, for example) to
disclose that partner on the listing. A profile whose reference listing names
none gets a warning on the review card. It is a warning, not a block: a seller
who really makes the item themselves needs no partner, and only they know.
"""

from __future__ import annotations

from typing import Any

#: The app's product templates whose items are normally printed by a partner.
POD_TEMPLATES = frozenset({"apparel"})
#: Etsy's ``who_made`` values that say somebody other than the seller made it.
MADE_BY_OTHERS = frozenset({"someone_else", "collective"})


def partner_expected(profile: Any) -> bool:
    """Whether ``profile``'s items look made by a partner: a print-on-demand
    template, or a reference listing that says someone else made it. False while
    its reference has not been read (nothing is known)."""
    payload = getattr(profile, "cached_payload", None) or {}
    if not payload:
        return False
    return payload.get("who_made") in MADE_BY_OTHERS or getattr(profile, "content_template", "") in POD_TEMPLATES


def production_note(profile: Any) -> str | None:
    """The warning for a listing built with ``profile``, or None.

    None while the profile's reference has not been read (nothing is known), when
    it names a production partner, or when nothing suggests a partner is involved.
    """
    payload = getattr(profile, "cached_payload", None) or {}
    if not partner_expected(profile) or payload.get("production_partner_ids"):
        return None
    by_others = payload.get("who_made") in MADE_BY_OTHERS
    name = getattr(profile, "name", "") or "this profile"
    said = (
        f"Its reference listing says the item is made by {'another company or person' if payload.get('who_made') == 'someone_else' else 'others'}, "
        "but names no production partner."
        if by_others
        else "Its reference listing names no production partner."
    )
    return (
        f"Profile “{name}” has no production partner. {said} If a production partner makes this item "
        "(a print-on-demand provider, for example), Etsy's Creativity Standards require the listing to say so: "
        "add the partner to the reference listing on Etsy, then refresh the profile. If you make it yourself, "
        "no partner is needed."
    )
