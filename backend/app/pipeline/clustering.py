"""Cluster the seller's own listings into candidate profiles (Section B refinement).

Instead of the seller hand-picking reference listings, we group their active
listings by the attributes that make two listings "the same kind of product":
taxonomy, production partner, variation structure and price band. Each cluster
becomes one candidate profile whose reference is the most complete listing in it.
Pure functions only — the Etsy fetch and LLM naming happen in the worker. Only the
authenticated seller's own listings are ever passed in here.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class ListingForCluster:
    listing_id: int
    title: str
    taxonomy_id: int | None
    price: float | None
    production_partner_ids: tuple[int, ...]
    variation_properties: tuple[str, ...]  # e.g. ("Size", "Color")
    image_count: int


@dataclass
class Cluster:
    key: tuple
    listings: list[ListingForCluster]

    @property
    def reference(self) -> ListingForCluster:
        """The most complete listing: most images, then most variations, then id."""
        return max(
            self.listings,
            key=lambda listing: (
                listing.image_count,
                len(listing.variation_properties),
                listing.listing_id,
            ),
        )

    @property
    def has_size_variation(self) -> bool:
        return any("size" in p.lower() for p in self.reference.variation_properties)


def _price_band(price: float | None, width: float) -> int | None:
    if price is None:
        return None
    return int(price // width)


def _cluster_key(listing: ListingForCluster, band_width: float) -> tuple:
    return (
        listing.taxonomy_id,
        tuple(sorted(listing.production_partner_ids)),
        _price_band(listing.price, band_width),
        tuple(sorted({p.lower() for p in listing.variation_properties})),
    )


def cluster_listings(
    listings: list[ListingForCluster], *, band_width: float = 10.0
) -> list[Cluster]:
    """Group listings into candidate-profile clusters (largest cluster first)."""
    groups: dict[tuple, list[ListingForCluster]] = {}
    for listing in listings:
        groups.setdefault(_cluster_key(listing, band_width), []).append(listing)
    clusters = [Cluster(key=key, listings=members) for key, members in groups.items()]
    clusters.sort(key=lambda c: len(c.listings), reverse=True)
    return clusters


_NAME_STOP = {
    "the", "and", "for", "with", "a", "an", "of", "to", "in", "on", "gift", "gifts",
    "men", "women", "kids", "unisex", "cute", "funny", "custom", "personalized", "new",
}
_WORD = re.compile(r"[A-Za-z][A-Za-z']+")


def heuristic_name(titles: list[str], *, max_words: int = 2) -> str:
    """Fallback cluster name when no LLM is available: the most shared keywords."""
    counts: Counter[str] = Counter()
    display: dict[str, str] = {}
    for title in titles:
        seen: set[str] = set()
        for word in _WORD.findall(title):
            low = word.lower()
            if low in _NAME_STOP or len(low) < 3 or low in seen:
                continue
            seen.add(low)
            counts[low] += 1
            display.setdefault(low, word)
    if not counts:
        return "Profile"
    top = [display[word] for word, _ in counts.most_common(max_words)]
    return " ".join(top)
