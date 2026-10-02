"""Old vs new listing rules on a fixed set of real designs.

    python -m evals.content_eval build    # once: analyse the designs, freeze the set
    python -m evals.content_eval run      # write both listings for every design, judge, score
    python -m evals.content_eval report   # the side-by-side report

The set (``evals/data/content_set.json``) is fixed: the vision analysis of each
design, the search phrases a buyer might type for it, and the attributes the
design supports. Both prompts are given exactly the same analysis. The set and
the results hold the seller's own designs' text, so ``evals/data`` is not
committed.

Rubric, each 0-100 (see ``score``):

1. search-phrase coverage  share of the set's buyer phrases the listing can match
                           (every word of the phrase is somewhere in the title, tags,
                           description opening or attributes; any garment word counts
                           as any other)
2. distinct tag intents    kinds of search the 13 tags serve, labelled by a blind judge
3. no repetition           words not repeated in the title; tags that add to the title
4. readability             a blind judge's 1-5 for the title, and Etsy's "fewer than 15 words"
5. attribute completeness  attributes the design supports that the listing fills

No Etsy request is made. Nothing here measures ranking: Etsy publishes no
ranking data, so this checks the listing against Etsy's published guidance.
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.compliance.trademarks import configured_blocklist
from app.core.config import get_settings
from app.pipeline import search_rules as sr
from app.pipeline.content import (
    AnthropicContentGenerator,
    ContentValidationError,
    GeneratedListing,
    policy_for,
)
from app.pipeline.llm import LLMError, client_for
from app.pipeline.templates import load_template
from app.pipeline.vision import AnthropicVisionAnalyzer, VisionAnalysis

DATA = Path(__file__).parent / "data"
SET = DATA / "content_set.json"
RESULTS = DATA / "content_results.json"
REPORT = DATA / "content_report.md"

#: The developer's own account: the designs are the seller's own uploads.
ACCOUNT = "dev@localhost"
#: group key -> the kind of design it is in the set.
DESIGNS: dict[str, str] = {
    "AD2-COMFORT": "single-theme",
    "AD4": "single-theme",
    "br5013": "single-theme",
    "br5024-COMFORT": "single-theme",
    "br5336-COMFORT": "single-theme",
    "BR4615-COMFORT": "single-theme",
    "br5199-COMFORT": "single-theme",
    "BR5229": "dual-theme",
    "BR5370-COMFORT": "dual-theme",
    "BR5367": "dual-theme",
    "br4957": "dual-theme",
    "BR4834": "dual-theme",
    "AD3": "text-heavy",
    "BR5436": "text-heavy",
    "br5078": "text-heavy",
    "br5109-COMFORT": "text-heavy",
    "BR5449": "text-heavy",
    "BR5424": "text-heavy",
    "br4920-COMFORT": "character",
    "BR5348-COMFORT": "character",
}
CONCURRENCY = 4

GOLD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "phrases": {"type": "array", "items": {"type": "string"}},
        "attributes": {
            "type": "object",
            "properties": {k: {"type": "string"} for k in sr.ATTRIBUTE_KEYS},
            "required": list(sr.ATTRIBUTE_KEYS),
            "additionalProperties": False,
        },
    },
    "required": ["phrases", "attributes"],
    "additionalProperties": False,
}
GOLD_SYSTEM = """You are building a fixed test set for evaluating Etsy listings of printed apparel.

From the analysis of one design, write:

1. phrases: exactly 12 search phrases that real buyers would type into Etsy search to find a garment with this design. Each is 2 to 4 lowercase words. Make them different searches by different buyers: the main subject with a garment word, the recipient or gift angle, the occasion or holiday, the profession or community, the season, the style or era, the kind of humour, a second theme if the design has one. Use ordinary garment words (shirt, tee, sweatshirt). Never use a brand, franchise or character name, and never the words printed on the design unless buyers search for them.

2. attributes: for each of occasion, holiday, style, primary_color, theme, recipient, the short value the design clearly supports, or an empty string when the design does not support that attribute. Be strict: fill only what the analysis shows.

You are describing what buyers search for, not writing a listing."""

JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        name: {
            "type": "object",
            "properties": {
                "tag_intents": {"type": "array", "items": {"type": "string", "enum": [*sr.INTENTS, "none"]}},
                "readability": {"type": "integer"},
                "reason": {"type": "string"},
            },
            "required": ["tag_intents", "readability", "reason"],
            "additionalProperties": False,
        }
        for name in ("A", "B")
    },
    "required": ["A", "B"],
    "additionalProperties": False,
}
JUDGE_SYSTEM = """You assess two Etsy listings, A and B, written for the same printed-apparel design. You do not know how either was produced. Judge each on its own.

For each listing:

- tag_intents: for each of its tags, in order, the one kind of buyer search the tag serves:
  recipient (who it is bought for), occasion (a holiday or event), profession (a job or community), season, style (a look or era), humor (a kind of joke), product (a garment word or how it is worn), subject (what the design shows or is about), or none (a tag no buyer would search, or one that is only generic filler).
  Return exactly one label per tag.

- readability: how well the TITLE works for a buyer scanning a page of search results, 1 to 5:
  5 = reads like a clear product name; you know at once what the item is
  4 = clear, slightly long or with one awkward phrase
  3 = understandable but reads partly like a keyword list
  2 = mostly a keyword list; repeats itself
  1 = a string of keywords; hard to say what the item is

- reason: one short sentence for the readability score."""


def _media_type(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _analysis_text(a: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"Themes, most dominant first: {', '.join(a['themes'])}",
            f"Meaning: {a['meaning']}",
            f"Recipient: {a['recipient'] or '(none)'}",
            f"Humor: {a['humor'] or '(none)'}",
            f"Profession: {a['profession'] or '(none)'}",
            f"Season: {a['season'] or '(none)'}",
            f"Occasion: {a['occasion'] or '(none)'}",
            f"Style: {a['style']}",
            f"Colors: {', '.join(a['colors'])}",
            f"Target audience: {a['target_audience']}",
            f"Printed text: {a['embedded_text'] or '(none)'}",
        ]
    )


async def _json(client: Any, **kw: Any) -> dict[str, Any]:
    """One structured answer; an unparseable one is asked for again."""
    for attempt in range(3):
        try:
            return (await client.complete_json(**kw)).data
        except LLMError:
            if attempt == 2:
                raise
    raise AssertionError


async def _gather(jobs: list[Any]) -> list[Any]:
    gate = asyncio.Semaphore(CONCURRENCY)

    async def one(job: Any) -> Any:
        async with gate:
            return await job

    return await asyncio.gather(*(one(j) for j in jobs))


# --- build: freeze the set -----------------------------------------------------------------------


async def build() -> None:
    from sqlalchemy import select

    from app.api.deps import get_storage
    from app.db.models import Asset, GeneratedContent, Tenant
    from app.db.session import get_sessionmaker

    settings = get_settings()
    storage = get_storage()
    analyzer = AnthropicVisionAnalyzer(client_for(settings, "vision"))
    writer = client_for(settings, "content")

    async with get_sessionmaker()() as s:
        tenant = (await s.execute(select(Tenant).where(Tenant.email == ACCOUNT))).scalar_one()
        covers: dict[str, Asset] = {}
        rows = await s.execute(
            select(Asset)
            .join(GeneratedContent, GeneratedContent.asset_id == Asset.id)
            .where(Asset.tenant_id == tenant.id, Asset.processed_key.is_not(None))
            .order_by(GeneratedContent.created_at)
        )
        for asset in rows.scalars():
            if asset.group_key in DESIGNS:
                covers[asset.group_key] = asset
    missing = sorted(set(DESIGNS) - set(covers))
    if missing:
        print("not found in the account, left out:", missing)

    async def one(key: str, asset: Any) -> dict[str, Any]:
        data = storage.get(asset.processed_key)
        analysis = asdict((await analyzer.analyze(data, _media_type(data))).analysis)
        gold = await _json(
            writer,
            system=GOLD_SYSTEM,
            content_blocks=[{"type": "text", "text": _analysis_text(analysis)}],
            schema=GOLD_SCHEMA,
            max_tokens=2048,
        )
        return {
            "id": key,
            "kind": DESIGNS[key],
            "analysis": analysis,
            "phrases": [str(p).strip().lower() for p in gold["phrases"]],
            "supported_attributes": sr.clean_attributes(gold["attributes"]),
        }

    designs = await _gather([one(k, a) for k, a in covers.items()])
    designs.sort(key=lambda d: (list(dict.fromkeys(DESIGNS.values())).index(d["kind"]), d["id"].lower()))
    DATA.mkdir(exist_ok=True)
    SET.write_text(json.dumps({"account": ACCOUNT, "designs": designs}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"froze {len(designs)} designs in {SET}")


# --- run: both prompts, judge, score ---------------------------------------------------------------


def _generators() -> dict[str, AnthropicContentGenerator]:
    client = client_for(get_settings(), "content")
    policy = policy_for("apparel")
    return {
        "old": AnthropicContentGenerator(client, template=load_template("content/apparel"), policy=policy),
        "new": AnthropicContentGenerator(
            client,
            template=load_template("content/apparel_search"),
            policy=policy,
            title_rules=sr.SEARCH_TITLE,
            max_tokens=1536,
        ),
    }


async def _write(generator: AnthropicContentGenerator, analysis: VisionAnalysis, sku: str) -> dict[str, Any]:
    try:
        for attempt in range(3):
            try:
                result = await generator.generate(analysis, sku)
                break
            except LLMError:
                if attempt == 2:
                    raise
    except ContentValidationError as exc:
        return {"failed": exc.errors, "attempts": 2}
    item: GeneratedListing = result.listing
    return {
        "title": item.title,
        "tags": item.tags,
        # What the description starts with on Etsy. The old flow discards the
        # model's description and puts the title above the shop's own text.
        "opening": item.opening or item.title,
        "attributes": item.attributes,
        "own_intents": item.tag_intents,
        "attempts": result.attempts,
    }


async def _judge(client: Any, design: dict[str, Any], old: dict[str, Any], new: dict[str, Any], rng: random.Random) -> None:
    if "failed" in old or "failed" in new:
        return
    flip = rng.random() < 0.5
    a, b = (new, old) if flip else (old, new)
    text = "\n\n".join(
        [
            "The design:\n" + _analysis_text(design["analysis"]),
            *(
                f"Listing {name}\nTitle: {item['title']}\nTags:\n" + "\n".join(f"- {t}" for t in item["tags"])
                for name, item in (("A", a), ("B", b))
            ),
        ]
    )
    out = await _json(
        client, system=JUDGE_SYSTEM, content_blocks=[{"type": "text", "text": text}], schema=JUDGE_SCHEMA, max_tokens=2048
    )
    for name, item in (("A", a), ("B", b)):
        verdict = out[name]
        item["judge_intents"] = [str(i) for i in verdict["tag_intents"]][: len(item["tags"])]
        item["readability"] = max(1, min(5, int(verdict["readability"])))
        item["readability_reason"] = str(verdict["reason"])


_GARMENT = {"shirt", "tshirt", "tee", "sweatshirt", "hoodie", "crewneck", "top", "pullover"}


def _search_words(text: str) -> list[str]:
    # Which garment word a buyer typed is not what is being measured: a listing
    # that names the garment at all is given every garment word.
    return ["garment" if w in _GARMENT else w for w in sr.keywords(text)]


def covered(phrase: str, text: str) -> bool:
    """Whether a search for ``phrase`` can match ``text``: every word of it is there."""
    have = set(_search_words(text))
    need = _search_words(phrase)
    return bool(need) and all(w in have for w in need)


def usable(design: dict[str, Any]) -> dict[str, Any]:
    """The design without the phrases and attributes no listing may use: a brand
    or character name is refused by the trademark filter under either prompt."""
    marks = configured_blocklist()
    return {
        **design,
        "phrases": [p for p in design["phrases"] if not marks.find(p)],
        "supported_attributes": {k: v for k, v in design["supported_attributes"].items() if not marks.find(v)},
    }


def score(design: dict[str, Any], item: dict[str, Any]) -> dict[str, float]:
    """The five rubric scores for one listing, each 0-100."""
    title, tags = item["title"], item["tags"]
    everything = " ".join([title, *tags, item["opening"], *item["attributes"].values()])
    phrases = design["phrases"]
    coverage = sum(covered(p, everything) for p in phrases) / len(phrases)
    coverage_title_tags = sum(covered(p, " ".join([title, *tags])) for p in phrases) / len(phrases)

    kinds = {i for i in item.get("judge_intents", []) if i in sr.INTENTS}
    intents = min(1.0, len(kinds) / 6)  # six of the eight kinds is a full set for one design

    words = sr.keywords(title)
    repeated = len(words) - len(set(words))
    in_title = set(words)
    adds = [t for t in tags if not (sr.keywords(t) and set(sr.keywords(t)) <= in_title)]
    tag_sets = {frozenset(sr.keywords(t)) for t in tags}
    no_repeat = (
        (1 - min(1.0, repeated / max(1, len(words)) * 3))  # a third of the words repeated = 0
        + len(adds) / max(1, len(tags))
        + len(tag_sets) / max(1, len(tags))
    ) / 3

    under_15 = 1.0 if len(title.split()) < 15 else 0.0
    readability = ((item.get("readability", 1) - 1) / 4 + under_15) / 2

    supported = design["supported_attributes"]
    filled = [k for k in supported if item["attributes"].get(k)]
    attributes = len(filled) / len(supported) if supported else 1.0

    out = {
        "coverage": coverage,
        "intents": intents,
        "no_repetition": no_repeat,
        "readability": readability,
        "attributes": attributes,
    }
    out["overall"] = sum(out.values()) / len(out)
    out["coverage_title_tags_only"] = coverage_title_tags
    return {k: round(v * 100, 1) for k, v in out.items()}


async def run() -> None:
    designs = json.loads(SET.read_text(encoding="utf-8"))["designs"]
    generators = _generators()
    judge = client_for(get_settings(), "content")
    rng = random.Random(20261002)

    async def one(design: dict[str, Any]) -> dict[str, Any]:
        design = usable(design)
        analysis = VisionAnalysis(**design["analysis"])
        old, new = await asyncio.gather(
            _write(generators["old"], analysis, design["id"]), _write(generators["new"], analysis, design["id"])
        )
        await _judge(judge, design, old, new, rng)
        for item in (old, new):
            if "failed" not in item:
                item["scores"] = score(design, item)
        print("done", design["id"], flush=True)
        return {"id": design["id"], "kind": design["kind"], "old": old, "new": new}

    results = await _gather([one(d) for d in designs])
    RESULTS.write_text(json.dumps({"model": judge.model, "results": results}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {RESULTS}")
    report()


# --- report -----------------------------------------------------------------------------------------

RUBRIC = ["coverage", "intents", "no_repetition", "readability", "attributes", "overall"]
LABELS = {
    "coverage": "Search-phrase coverage",
    "intents": "Distinct tag intents",
    "no_repetition": "No repetition",
    "readability": "Readability",
    "attributes": "Attribute completeness",
    "overall": "Overall",
    "coverage_title_tags_only": "Coverage, title + tags only",
}


def _mean(rows: list[dict[str, Any]], side: str, key: str) -> str:
    values = [r[side]["scores"][key] for r in rows if "scores" in r[side]]
    return f"{sum(values) / len(values):.0f}" if values else "-"


def report() -> None:
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    everything = data["results"]
    # Averages compare like with like: only designs both prompts produced a
    # listing for (the judge sees the two together). Failures are counted apart.
    rows = [r for r in everything if "readability" in r["old"] and "readability" in r["new"]]
    designs = {d["id"]: usable(d) for d in json.loads(SET.read_text(encoding="utf-8"))["designs"]}
    out: list[str] = [
        "# Listing rules: old vs new",
        "",
        f"{len(everything)} designs from {ACCOUNT}, both prompts given the same frozen analysis, model `{data['model']}`.",
        f"Averages are over the {len(rows)} designs for which both prompts produced a listing.",
        "Scores are 0-100. They measure the listing against Etsy's published guidance, not ranking.",
        "",
        "## Averages",
        "",
        "| | Old | New |",
        "|---|---|---|",
    ]
    for key in [*RUBRIC, "coverage_title_tags_only"]:
        out.append(f"| {LABELS[key]} | {_mean(rows, 'old', key)} | {_mean(rows, 'new', key)} |")
    for side in ("old", "new"):
        ok = [r[side] for r in everything if "scores" in r[side]]
        failed = [r["id"] for r in everything if "failed" in r[side]]
        retries = sum(1 for i in ok if i["attempts"] > 1)
        lengths = [len(i["title"]) for i in ok]
        wordcounts = [len(i["title"].split()) for i in ok]
        out += [
            "",
            f"**{side.capitalize()}**: {len(ok)} written, {retries} needed the one retry, {len(failed)} failed validation"
            + (f" ({', '.join(failed)})" if failed else "")
            + (f". Titles {min(lengths)}-{max(lengths)} characters (mean {sum(lengths) / len(lengths):.0f}), "
               f"{min(wordcounts)}-{max(wordcounts)} words (mean {sum(wordcounts) / len(wordcounts):.1f})." if ok else "."),
        ]
    out += ["", "## By kind of design", "", "| Kind | n | Old overall | New overall | Old coverage | New coverage | Old readability | New readability |", "|---|---|---|---|---|---|---|---|"]
    for kind in dict.fromkeys(r["kind"] for r in rows):
        group = [r for r in rows if r["kind"] == kind]
        out.append(
            f"| {kind} | {len(group)} | {_mean(group, 'old', 'overall')} | {_mean(group, 'new', 'overall')} | "
            f"{_mean(group, 'old', 'coverage')} | {_mean(group, 'new', 'coverage')} | "
            f"{_mean(group, 'old', 'readability')} | {_mean(group, 'new', 'readability')} |"
        )
    out += ["", "## Every design", ""]
    for r in everything:
        d = designs[r["id"]]
        out += [
            f"### {r['id']} ({r['kind']})",
            "",
            f"Themes: {', '.join(d['analysis']['themes'])}. Printed text: \"{d['analysis']['embedded_text']}\"",
            "",
            f"Buyer phrases in the set: {', '.join(d['phrases'])}",
            "",
            f"Attributes the design supports: {', '.join(f'{k}={v}' for k, v in d['supported_attributes'].items()) or 'none'}",
            "",
        ]
        for side in ("old", "new"):
            item = r[side]
            out.append(f"**{side.capitalize()}**")
            out.append("")
            if "failed" in item:
                out += ["Failed validation after the retry: " + "; ".join(item["failed"]), ""]
                continue
            s = item["scores"]
            out += [
                f"- Title ({len(item['title'])} chars, {len(item['title'].split())} words): {item['title']}",
                f"- Tags: {', '.join(item['tags'])}",
                f"- Tag intents (judge): {', '.join(sorted({i for i in item.get('judge_intents', []) if i in sr.INTENTS}))}",
                f"- Description opens with: {item['opening']}",
                f"- Attributes: {', '.join(f'{k}={v}' for k, v in item['attributes'].items()) or 'none written'}",
                (
                    f"- Judge on the title: {item['readability']}/5, {item.get('readability_reason', '')}"
                    if "readability" in item
                    else "- Not judged: the other prompt produced no listing to compare with"
                ),
                (
                    "- Scores: " + ", ".join(f"{LABELS[k].lower()} {s[k]:.0f}" for k in RUBRIC)
                    + f" (coverage from title + tags alone {s['coverage_title_tags_only']:.0f})"
                    if "readability" in item
                    else f"- Coverage {s['coverage']:.0f}, no repetition {s['no_repetition']:.0f}, attributes {s['attributes']:.0f}"
                ),
                f"- Phrases not covered: {', '.join(p for p in d['phrases'] if not covered(p, ' '.join([item['title'], *item['tags'], item['opening'], *item['attributes'].values()]))) or 'none'}",
                "",
            ]
    REPORT.write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out[: out.index("## Every design")]))
    print(f"full report: {REPORT}")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "build":
        asyncio.run(build())
    elif command == "run":
        asyncio.run(run())
    elif command == "report":
        report()
    else:
        print(__doc__)
