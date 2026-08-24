"""Shop-section matching tests (spec §6)."""

from app.pipeline.sections import choose_section, load_section_rules, match_section_by_rules

RULES = {
    "4th of July": ["independence day", "july 4", "patriotic"],
    "Christmas": ["christmas", "santa"],
}


def test_rule_match_is_deterministic() -> None:
    assert match_section_by_rules("a patriotic eagle", RULES) == "4th of July"
    assert match_section_by_rules("abstract shapes", RULES) is None


def test_rule_hit_with_existing_section() -> None:
    d = choose_section(
        theme="patriotic eagle design",
        existing_sections=["4th of July", "Christmas"],
        rules=RULES,
    )
    assert d.name == "4th of July" and d.exists and not d.create


def test_rule_hit_missing_section_auto_create_off() -> None:
    # Matches a section the shop doesn't have; auto-create OFF -> do NOT create.
    d = choose_section(theme="santa hat", existing_sections=[], rules=RULES, auto_create=False)
    assert d.name == "Christmas" and not d.exists and not d.create


def test_rule_hit_missing_section_auto_create_on() -> None:
    d = choose_section(theme="santa hat", existing_sections=[], rules=RULES, auto_create=True)
    assert d.name == "Christmas" and d.create


def test_no_match_leaves_section_empty() -> None:
    d = choose_section(theme="random abstract art", existing_sections=["Christmas"], rules=RULES)
    assert d.name is None and not d.create


def test_llm_fallback_used_only_on_miss() -> None:
    calls = []

    def llm(text: str, existing: list[str]) -> str | None:
        calls.append(text)
        return "Christmas"

    d = choose_section(
        theme="winter cozy cabin",
        existing_sections=["Christmas"],
        rules=RULES,
        llm=llm,
    )
    assert calls and d.name == "Christmas" and d.exists


def test_bundled_rules_file_loads() -> None:
    rules = load_section_rules()
    assert "Christmas" in rules and "4th of July" in rules
