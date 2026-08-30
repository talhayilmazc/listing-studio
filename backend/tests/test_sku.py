"""SKU filename parsing tests."""

import pytest

from app.pipeline.sku import DEFAULT_SKU_RULES, SkuParser, SkuRule


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("SKU123_front.png", "SKU123"),
        ("ABC-001-back.jpg", "ABC-001"),
        ("TShirt-Red_2.jpg", "TSHIRT-RED"),
        ("DESIGN9.png", "DESIGN9"),
        ("mug-blue-main.webp", "MUG-BLUE"),
        ("poster_03.jpeg", "POSTER"),
    ],
)
def test_default_rules(filename: str, expected: str) -> None:
    assert SkuParser().parse(filename) == expected


def test_no_match_returns_none() -> None:
    # Spaces are not part of a clean SKU token and no view/index suffix present.
    assert SkuParser().parse("my holiday photo.png") is None


def test_normalize_can_be_disabled() -> None:
    parser = SkuParser(normalize=False)
    assert parser.parse("abc_front.png") == "abc"


def test_custom_rules_take_precedence() -> None:
    # Only accept an explicit "sku-XXXX" prefix.
    parser = SkuParser([SkuRule(r"sku-(?P<sku>\d{4})")])
    assert parser.parse("photo-sku-4821-front.png") == "4821"
    assert parser.parse("photo-front.png") is None


def test_default_ruleset_is_exposed() -> None:
    assert len(DEFAULT_SKU_RULES) >= 1


# --- SKU-at-end rule (spec §5) ---------------------------------------------
@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("tasarim_BR5475.png", "BR5475"),
        ("mockup-front-AB1234.jpg", "AB1234"),
        ("something_BR5475_2.png", "BR5475"),
    ],
)
def test_sku_suffix_rule(filename: str, expected: str) -> None:
    assert SkuParser().parse(filename) == expected


# --- SKU from folder name (spec §D2) ---------------------------------------
@pytest.mark.parametrize(
    ("folder", "expected"),
    [
        ("BR5475", "BR5475"),  # folder name is the SKU directly
        ("BR5475/", "BR5475"),  # trailing slash tolerated
        ("designs/BR5475", "BR5475"),  # nested: use the last segment
        ("Summer BR5475 Tee", "BR5475"),  # extra text -> extract the token
        ("plainfolder", "PLAINFOLDER"),  # clean token used as-is (normalized)
        ("has space text", None),  # not a token and no SKU pattern -> none
        ("", None),
        (None, None),
    ],
)
def test_parse_group(folder, expected) -> None:
    assert SkuParser().parse_group(folder) == expected


def test_folder_sku_takes_precedence_over_filename() -> None:
    # parse_group wins; the filename rule is only a fallback (D2).
    parser = SkuParser()
    assert parser.parse_group("BR5475") == "BR5475"
    assert parser.parse("photo_XY9999.png") == "XY9999"  # different fallback value
