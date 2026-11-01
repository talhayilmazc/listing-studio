"""The free tools run in the browser, so the rules are in TypeScript too; they must stay the app's rules.

Two files tie the copies to the app: the words and limits (frontend/lib/listingRuleData.ts) are written
from the Python rules, and the answers (fixtures/free_tool_cases.json) are read by both test suites.
Change a rule or the trademark list and this fails, saying what to write again.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from app.compliance import trademarks
from app.ops.free_tool_rules import render

ROOT = Path(__file__).resolve().parents[2]
if not (ROOT / "frontend").exists():  # in the test container the backend is /app and the repo's files are at /
    ROOT = Path("/")
FIXTURE = Path(__file__).parent / "fixtures" / "free_tool_cases.json"


def _cases_script():
    spec = importlib.util.spec_from_file_location("free_tool_cases", Path(__file__).parents[1] / "scripts" / "free_tool_cases.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_browser_has_the_apps_words_limits_and_trademark_list() -> None:
    written = (ROOT / "frontend/lib/listingRuleData.ts").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert written == render(), (
        "Write it again: docker compose exec -T api python -m app.cli free-tool-rules > frontend/lib/listingRuleData.ts"
    )


def test_the_shared_cases_are_what_the_apps_rules_answer() -> None:
    stored = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert stored == _cases_script().build(), "Write them again in the api container: python scripts/free_tool_cases.py"


def test_the_cases_really_exercise_the_rules() -> None:
    stored = json.loads(FIXTURE.read_text(encoding="utf-8"))
    titles = stored["titles"]
    assert {t["text"]: t["brands"] for t in titles}["DisneyWorld Family Trip Shirt"] == ["Disney"]
    assert {t["text"]: t["brands"] for t in titles}["Marvelous Scavenger Hunt Shirt"] == []
    assert {t["text"]: t["brands"] for t in titles}["Blue Y Neck Tee"] == []
    for key in ("repeated", "subjective", "commerce", "gift", "brands"):
        assert any(t[key] for t in titles), key
    assert {t["report"]["item_first"] for t in titles} == {True, False}
    tag_sets = stored["tag_sets"]
    assert any(c["same_search"] for c in tag_sets) and any(c["only_title_words"] for c in tag_sets)
    assert {c["etsy_ok"] for c in tag_sets} == {True, False}
    # The brand list the browser spots from is the bundled one, whatever an account's filter says.
    assert trademarks.bundled_blocklist().spot("Disney")
