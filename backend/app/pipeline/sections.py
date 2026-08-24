"""Shop-section auto-assignment from the vision analysis.

A deterministic keyword rule file (``prompts/sections/rules.json``) is checked
first (free, reproducible); only on a miss is the optional LLM fallback used.
Creating a new section in the user's shop is gated by ``AUTO_CREATE_SECTIONS``
(default off) — never open sections without permission.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

_RULES_PATH = Path(__file__).parent / "prompts" / "sections" / "rules.json"

# A callable that, given (text, existing_section_titles), returns a section name or None.
SectionLLM = Callable[[str, list[str]], str | None]


def load_section_rules(path: Path | None = None) -> dict[str, list[str]]:
    return json.loads((path or _RULES_PATH).read_text(encoding="utf-8"))


def match_section_by_rules(text: str, rules: dict[str, list[str]]) -> str | None:
    hay = text.lower()
    for section, keywords in rules.items():
        if any(kw.lower() in hay for kw in keywords):
            return section
    return None


@dataclass
class SectionDecision:
    name: str | None
    exists: bool  # a section with this name already exists in the shop
    create: bool  # a new section should be created for it


def choose_section(
    *,
    theme: str,
    occasion: str = "",
    existing_sections: Iterable[str],
    rules: dict[str, list[str]] | None = None,
    llm: SectionLLM | None = None,
    auto_create: bool = False,
) -> SectionDecision:
    existing = list(existing_sections)
    lowered = {s.lower(): s for s in existing}
    rules = rules if rules is not None else load_section_rules()
    text = f"{theme} {occasion}".strip()

    name = match_section_by_rules(text, rules)
    if name is None and llm is not None:
        name = llm(text, existing)

    if name is None:
        return SectionDecision(name=None, exists=False, create=False)
    if name.lower() in lowered:
        return SectionDecision(name=lowered[name.lower()], exists=True, create=False)
    # Matched a section name the shop doesn't have yet.
    return SectionDecision(name=name, exists=False, create=auto_create)
