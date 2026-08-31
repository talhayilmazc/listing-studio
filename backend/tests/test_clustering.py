"""Listing clustering + naming (auto-detected profiles, Section B refinement)."""

from app.pipeline.clustering import (
    Cluster,
    ListingForCluster,
    cluster_listings,
    heuristic_name,
)


def _L(listing_id, *, taxonomy=1, price=25.0, partners=(), variations=("Size",), images=3, title="X"):
    return ListingForCluster(
        listing_id=listing_id,
        title=title,
        taxonomy_id=taxonomy,
        price=price,
        production_partner_ids=tuple(partners),
        variation_properties=tuple(variations),
        image_count=images,
    )


def test_clusters_by_taxonomy_partner_variation_and_price_band() -> None:
    listings = [
        _L(1, taxonomy=100, partners=(7,), price=24.0, variations=("Size",), images=5),
        _L(2, taxonomy=100, partners=(7,), price=26.0, variations=("Size",), images=3),  # same cluster
        _L(3, taxonomy=100, partners=(9,), price=24.0, variations=("Size",)),  # diff partner
        _L(4, taxonomy=200, partners=(7,), price=24.0, variations=("Size", "Color")),  # diff taxonomy+vars
    ]
    clusters = cluster_listings(listings, band_width=10.0)
    # #1 and #2 (same taxonomy/partner/price-band/variation) cluster together.
    sizes = sorted(len(c.listings) for c in clusters)
    assert sizes == [1, 1, 2]
    two = next(c for c in clusters if len(c.listings) == 2)
    # Reference is the most complete (most images) of the pair.
    assert two.reference.listing_id == 1


def test_reference_prefers_most_images_then_variations() -> None:
    cluster = Cluster(
        key=(),
        listings=[_L(10, images=2, variations=("Size",)), _L(11, images=2, variations=("Size", "Color"))],
    )
    assert cluster.reference.listing_id == 11  # tie on images -> more variations wins


def test_has_size_variation_flag() -> None:
    assert Cluster(key=(), listings=[_L(1, variations=("Size",))]).has_size_variation is True
    assert Cluster(key=(), listings=[_L(1, variations=("Scent",))]).has_size_variation is False


def test_heuristic_name_picks_shared_keywords() -> None:
    name = heuristic_name(
        ["Comfort Colors Patriotic Tee", "Comfort Colors Retro Tee", "Comfort Colors Flag Tee"]
    )
    assert "Comfort" in name and "Colors" in name


def test_heuristic_name_fallback_when_all_stopwords() -> None:
    assert heuristic_name(["for the and", "of to in"]) == "Profile"
