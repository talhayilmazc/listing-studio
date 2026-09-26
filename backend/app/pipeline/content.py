"""Listing content generation: title, exactly 13 tags, and description.

Validates the model output against Etsy's structural limits before it can be
persisted. Structured outputs can't express "exactly 13 items" or per-item length
limits, so these are enforced here: an invalid result is regenerated **once**,
then reported as failed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from app.compliance.trademarks import Blocklist, configured_blocklist, trademark_errors
from app.pipeline.llm import LLMClient, LLMError, Usage
from app.pipeline.templates import PromptTemplate, load_template
from app.pipeline.vision import VisionAnalysis

# Etsy structural limits.
REQUIRED_TAG_COUNT = 13
MIN_TITLE_LENGTH = 110
MAX_TITLE_LENGTH = 140
# How the too-short title error begins (targets.py relaxes it for trimmed titles).
TITLE_TOO_SHORT = "title must be at least"
MAX_TAG_LENGTH = 20


@dataclass(frozen=True)
class ContentPolicy:
    """Product-type content rules, scoped to a profile's ``content_template``.

    Forbidden words are deliberately NOT global: "digital download" is wrong on an
    apparel listing but exactly right for a digital-products seller. Which policy
    applies is decided by the profile (see :func:`policy_for`).
    """

    forbidden_terms: tuple[str, ...] = ()
    required_type_terms: tuple[str, ...] = ()
    #: Generic, no-search-value phrases banned in TAGS only (title may differ).
    forbidden_tag_terms: tuple[str, ...] = ()
    #: Filler that spends a slot without being searched, banned in the title AND
    #: the tags (v6 §B).
    forbidden_filler_terms: tuple[str, ...] = ()
    #: The title describes the design; it must not transcribe the words printed
    #: on it (v6 §B). Checked at generation, where the design's text is known.
    describe_not_transcribe: bool = False


# File-format / delivery words that must never appear on a physical apparel listing.
_APPAREL_FORBIDDEN = (
    "svg",
    "png",
    "pdf",
    "printable",
    "digital download",
    "instant download",
    "cut file",
    "sublimation",
    "clipart",
)
# At least one of these must name the product type in the title and in the tags.
_APPAREL_TYPES = ("shirt", "t-shirt", "tshirt", "tee", "sweatshirt", "hoodie")
# Generic design adjectives that carry no search value -> rejected as tags (v4 §H).
_GENERIC_DESIGN_TAGS = (
    "illustrated design",
    "graphic design",
    "digital art",
    "printed design",
    "custom design",
    "unique design",
    "trendy design",
    "cool design",
)

# Words that describe how any printed design was made, not what it is about:
# nobody searches "illustration shirt" (v6 §B).
_FILLER = (
    "hand drawn",
    "hand-drawn",
    "handdrawn",
    "illustration",
    "artwork",
    "design tee",
    "graphic print",
)

_POLICIES: dict[str, ContentPolicy] = {
    "apparel": ContentPolicy(
        _APPAREL_FORBIDDEN,
        _APPAREL_TYPES,
        _GENERIC_DESIGN_TAGS,
        forbidden_filler_terms=_FILLER,
        describe_not_transcribe=True,
    ),
    # Digital sellers may legitimately use "digital download", "SVG", etc.
    "digital_products": ContentPolicy(),
}


def policy_for(content_template: str) -> ContentPolicy:
    """Return the content policy for a profile's ``content_template`` (default: none)."""
    return _POLICIES.get(content_template, ContentPolicy())


def _has_term(text: str, term: str) -> bool:
    """Whole-word (case-insensitive) match, so 'png' won't fire inside 'opening'."""
    return re.search(rf"\b{re.escape(term)}\b", text, re.IGNORECASE) is not None


# A title that repeats this many consecutive words of the design's printed text
# is transcribing it. Three is allowed: "Labor and Delivery Nurse Shirt" names the
# job, and a short phrase is often exactly what buyers search for.
COPIED_RUN_WORDS = 4


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower().replace("'", "").replace("\u2019", ""))


def copied_design_text(title: str, embedded_text: str) -> str | None:
    """The longest run of at least COPIED_RUN_WORDS consecutive words of the
    design's text that the title repeats, or None."""
    source = _words(embedded_text)
    target = _words(title)
    if len(source) < COPIED_RUN_WORDS or len(target) < COPIED_RUN_WORDS:
        return None
    best: list[str] = []
    for i in range(len(source)):
        for j in range(len(target)):
            k = 0
            while i + k < len(source) and j + k < len(target) and source[i + k] == target[j + k]:
                k += 1
            if k > len(best):
                best = source[i : i + k]
    return " ".join(best) if len(best) >= COPIED_RUN_WORDS else None


# Words that say nothing about which theme a phrase is (v7 §A1 matching).
_NOT_THEME_WORDS = {
    "the", "and", "for", "with", "funny", "cute", "gift", "gifts", "design", "shirt", "tee",
    "tshirt", "sweatshirt", "hoodie", "graphic", "humor", "humour", "theme", "themed", "day",
    "lover", "lovers", "life", "season", "vintage", "retro",
}


def _theme_words(theme: str) -> list[str]:
    return [w for w in _words(theme) if len(w) >= 3 and w not in _NOT_THEME_WORDS]


def _mentions(text: str, theme: str) -> bool:
    """Whether ``text`` names ``theme``, allowing inflections ("nurse"/"nursing")."""
    words = _words(text)
    for key in _theme_words(theme):
        stem = key[: max(4, len(key) - 3)] if len(key) >= 5 else key
        if any(w == key or (len(stem) >= 4 and w.startswith(stem)) for w in words):
            return True
    return False


def theme_errors(
    listing: GeneratedListing, themes: list[str], blocklist: Blocklist | None = None
) -> list[str]:
    """A design with a second theme must carry it too (v7 §A1).

    The title names the second theme as well as the first, and the tags cover
    both. Only the two most dominant themes are required; a theme made only of
    generic words, or one on the trademark list, is not checked.
    """
    # A theme that is a trademark (the design shows a brand's character) can
    # never be named, so it is not asked for; the next theme takes its place.
    blocklist = configured_blocklist() if blocklist is None else blocklist
    top = [t for t in themes if _theme_words(t) and not blocklist.find(t)][:2]
    if len(top) < 2:
        return []
    errors: list[str] = []
    primary, secondary = top
    if not _mentions(listing.title, secondary):
        errors.append(
            f"the design has a second theme, '{secondary}', and buyers search for it too: "
            f"keep '{primary}' first and name '{secondary}' in the title as well "
            f"(e.g. a phrase combining both)"
        )
    for theme in top:
        if not any(_mentions(tag, theme) for tag in listing.tags):
            errors.append(f"add tags for the theme '{theme}': none of the tags names it")
    return errors


def copied_design_run(title: str, source: str, min_words: int) -> str | None:
    """The longest run of at least ``min_words`` consecutive words of ``source``
    that ``title`` repeats, or None."""
    a, b = _words(source), _words(title)
    best: list[str] = []
    for i in range(len(a)):
        for j in range(len(b)):
            k = 0
            while i + k < len(a) and j + k < len(b) and a[i + k] == b[j + k]:
                k += 1
            if k > len(best):
                best = a[i : i + k]
    return " ".join(best) if len(best) >= min_words else None


#: Following an example's pattern may repeat short stock phrases ("Gift for Her");
#: five words in a row is copying its title (v7 §B).
EXAMPLE_RUN_WORDS = 5


def example_errors(title: str, example_title: str) -> list[str]:
    run = copied_design_run(title, example_title, EXAMPLE_RUN_WORDS)
    if run is None and title.strip().casefold() != example_title.strip().casefold():
        return []
    return [
        f"the title copies the example listing ('{run or example_title}'): keep its pattern "
        "(the kinds of phrases and their order) but take every subject from this design"
    ]


def copied_text_errors(title: str, embedded_text: str) -> list[str]:
    run = copied_design_text(title, embedded_text)
    if run is None:
        return []
    return [
        f"the title copies the words printed on the design ('{run}'). Buyers do not "
        "search for a shirt's slogan: describe what the design is about instead (the "
        "profession, occasion, recipient, kind of humor or style), e.g. 'Funny Nurse "
        "Shirt, Flu Season Humor' rather than the printed joke"
    ]

CONTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "description": {"type": "string"},
    },
    "required": ["title", "tags", "description"],
    "additionalProperties": False,
}


@dataclass
class GeneratedListing:
    title: str
    tags: list[str]
    description: str


@dataclass
class ContentResult:
    listing: GeneratedListing
    usages: list[Usage]  # one per attempt (failed attempts still cost tokens)
    attempts: int


class ContentValidationError(Exception):
    """Output failed validation after the allowed retries."""

    def __init__(self, errors: list[str], usages: list[Usage]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors
        self.usages = usages


def validate_listing(
    listing: GeneratedListing,
    policy: ContentPolicy | None = None,
    *,
    trademarks: Blocklist | None = None,
) -> list[str]:
    """Return validation errors (empty if valid).

    Structural Etsy limits always apply; ``policy`` adds product-type rules
    (forbidden format words, required product-type wording) scoped to the profile.
    The trademark blocklist applies to every product type; ``trademarks=None``
    means the configured one (empty while TRADEMARK_FILTER is off).
    """
    errors: list[str] = []

    title = listing.title.strip()
    n = len(listing.title)
    if not title:
        errors.append("title is empty")
    elif n < MIN_TITLE_LENGTH:
        errors.append(
            f"{TITLE_TOO_SHORT} {MIN_TITLE_LENGTH} characters "
            f"(yours was {n}); target {MIN_TITLE_LENGTH}-{MAX_TITLE_LENGTH}"
        )
    if n > MAX_TITLE_LENGTH:
        errors.append(
            f"title exceeds {MAX_TITLE_LENGTH} characters "
            f"(yours was {n}); target {MIN_TITLE_LENGTH}-{MAX_TITLE_LENGTH}"
        )

    if len(listing.tags) != REQUIRED_TAG_COUNT:
        errors.append(f"expected exactly {REQUIRED_TAG_COUNT} tags, got {len(listing.tags)}")

    seen: set[str] = set()
    for tag in listing.tags:
        stripped = tag.strip()
        if not stripped:
            errors.append("a tag is empty")
            continue
        if len(tag) > MAX_TAG_LENGTH:
            errors.append(f"tag exceeds {MAX_TAG_LENGTH} characters: {tag!r}")
        if "," in tag:
            errors.append(f"tag contains a comma: {tag!r}")
        key = stripped.lower()
        if key in seen:
            errors.append(f"duplicate tag: {tag!r}")
        seen.add(key)

    if not listing.description.strip():
        errors.append("description is empty")

    if policy is not None:
        errors.extend(_policy_errors(listing, policy))

    blocklist = configured_blocklist() if trademarks is None else trademarks
    errors.extend(
        trademark_errors(listing.title, list(listing.tags), listing.description, blocklist)
    )

    return errors


def _policy_errors(listing: GeneratedListing, policy: ContentPolicy) -> list[str]:
    """Profile-scoped content rules: banned format words + required product type."""
    errors: list[str] = []
    title = listing.title
    tags = listing.tags

    for term in policy.forbidden_terms:
        if _has_term(title, term):
            errors.append(
                f"remove '{term}' from the title — not allowed for this product type"
            )
        bad = [t for t in tags if _has_term(t, term)]
        if bad:
            errors.append(
                f"remove '{term}' from these tags: {bad} — not allowed for this product type"
            )

    # Generic design adjectives carry no search value -> rejected as tags (v4 §H).
    for term in policy.forbidden_tag_terms:
        bad = [t for t in tags if _has_term(t, term)]
        if bad:
            errors.append(
                f"replace the generic tag(s) {bad}: '{term}' has no search value — "
                "each tag must name the subject, occasion, recipient, garment type or style"
            )

    # Filler with no search value, in the title or a tag (v6 §B).
    for term in policy.forbidden_filler_terms:
        if _has_term(title, term):
            errors.append(
                f"remove '{term}' from the title: it has no search value and wastes "
                "space; use a phrase a buyer would type (subject, occasion, recipient)"
            )
        bad = [t for t in tags if _has_term(t, term)]
        if bad:
            errors.append(
                f"replace the tag(s) {bad}: '{term}' has no search value — each tag "
                "must name the subject, occasion, recipient, garment type or style"
            )

    if policy.required_type_terms:
        options = ", ".join(policy.required_type_terms)
        if not any(_has_term(title, term) for term in policy.required_type_terms):
            errors.append(f"title must name the product type (one of: {options})")
        if not any(_has_term(tag, term) for tag in tags for term in policy.required_type_terms):
            errors.append(f"at least one tag must name the product type (e.g. {options})")

    return errors


def fit_title(title: str, min_length: int = MIN_TITLE_LENGTH, max_length: int = MAX_TITLE_LENGTH) -> str:
    """Bring a too-long title within ``max_length`` instead of failing it (v7 §A3).

    Titles are comma-separated phrases. Trailing phrases are dropped until it
    fits; if that leaves it under ``min_length``, the last dropped phrase comes
    back shortened word by word to the longest length that fits. A title that
    cannot land in the range either way is returned unchanged, for validation
    to report. The prefix is part of the first phrase, so it is never cut off.
    """
    title = title.strip()
    if len(title) <= max_length:
        return title
    phrases = [p.strip() for p in title.split(",") if p.strip()]
    join = ", ".join
    kept = list(phrases)
    dropped = ""
    while len(kept) > 1 and len(join(kept)) > max_length:
        dropped = kept.pop()
    if len(join(kept)) <= max_length and len(join(kept)) >= min_length:
        return join(kept)
    # Too short without the dropped phrase (or one phrase too long by itself):
    # the longest word-by-word cut of that phrase that fits.
    if len(join(kept)) > max_length:  # a single phrase: cut its own words
        dropped, kept = kept[0], []
    words = dropped.split()
    while words:
        candidate = join([*kept, " ".join(words)])
        if len(candidate) <= max_length:
            return candidate if len(candidate) >= min_length else title
        words.pop()
    return title


def join_prefix(prefix: str | None, title: str) -> str:
    """``prefix`` + the title's first phrase, joined by a space, never a comma.

    A title that already starts with the prefix (with or without a comma after
    it) gets it once, in the no-comma form.
    """
    prefix = (prefix or "").strip()
    raw = title.strip()
    if not prefix:
        return raw
    if raw.casefold().startswith(prefix.casefold()):
        raw = raw[len(prefix):].lstrip(" ,")
    return f"{prefix} {raw}".strip()


class ContentGenerator(Protocol):
    async def generate(
        self, analysis: VisionAnalysis, sku: str | None = None, pattern: dict[str, Any] | None = None
    ) -> ContentResult: ...


class AnthropicContentGenerator:
    def __init__(
        self,
        client: LLMClient,
        template: PromptTemplate | None = None,
        *,
        max_tokens: int = 1024,
        policy: ContentPolicy | None = None,
        title_prefix: str = "",
        trademarks: Blocklist | None = None,
    ) -> None:
        #: The account's trademark blocklist (v7 §A4); None = TRADEMARK_FILTER.
        self._trademarks = trademarks
        self._client = client
        self._template = template or load_template("content/digital_products")
        self._max_tokens = max_tokens
        self._policy = policy
        self._title_prefix = (title_prefix or "").strip()

    def _pattern_block(self, pattern: dict[str, Any]) -> dict[str, Any]:
        """The seller's own listing to follow (v7 §B): its pattern, not its subject."""
        tags = ", ".join(pattern.get("tags") or [])
        return {
            "type": "text",
            "text": (
                "Model this listing on one of the seller's own listings that has worked in "
                "their shop. Keep its pattern: the kinds of phrases in the title and their "
                "order (for example: subject and garment, then the humor, then the occasion, "
                "then a gift phrase for the recipient), and the kinds of tags it uses "
                "(occasion tags, recipient tags, garment tags, style tags). Take every "
                "subject, theme, profession and recipient from THIS design's analysis above, "
                "never from the example, and do not copy its title.\n"
                f"Example title: {pattern.get('title') or ''}\n"
                f"Example tags: {tags}"
            ),
        }

    def _content_blocks(self, analysis: VisionAnalysis, sku: str | None) -> list[dict[str, Any]]:
        text = self._template.render_user(
            {
                "theme": analysis.theme,
                "themes": ", ".join(analysis.themes or [analysis.theme]),
                "profession": analysis.profession or "(none)",
                "season": analysis.season or "(none)",
                "meaning": analysis.meaning or "(not given)",
                "embedded_text": analysis.embedded_text or "(none)",
                "style": analysis.style,
                "colors": ", ".join(analysis.colors),
                "target_audience": analysis.target_audience,
                "occasion": analysis.occasion or "(none)",
                "recipient": analysis.recipient or "(not given)",
                "humor": analysis.humor or "(none)",
                "product_type_hints": ", ".join(analysis.product_type_hints),
                "sku": sku or "(none)",
            }
        )
        blocks = [{"type": "text", "text": text}]
        if self._trademarks is not None and not self._trademarks:
            blocks.append(
                {
                    "type": "text",
                    "text": (
                        "This seller has turned the trademark filter off for their account: a "
                        "brand or character name may be used where the design itself shows it."
                    ),
                }
            )
        if self._title_prefix:
            # The prefix is prepended for us; the model writes only the remainder, to a
            # reduced budget so the FULL title still lands in MIN..MAX characters.
            used = len(self._title_prefix) + 1  # the space after it
            blocks.append(
                {
                    "type": "text",
                    "text": (
                        f"The title will begin with '{self._title_prefix} ' automatically, "
                        "joined to your first phrase with a space and no comma (e.g. "
                        f"'{self._title_prefix} Funny Nurse Shirt, ...'). Write ONLY the "
                        "phrases after that prefix, starting with the product phrase. The full "
                        f"title (prefix included) must be {MIN_TITLE_LENGTH}-{MAX_TITLE_LENGTH} "
                        f"characters, so write about {max(0, 130 - used)}-{MAX_TITLE_LENGTH - used} "
                        "characters and do not repeat the prefix."
                    ),
                }
            )
        return blocks

    def _apply_prefix(self, listing: GeneratedListing) -> GeneratedListing:
        """Prepend the fixed prefix to the generated title (v4: per-profile prefix).

        The prefix belongs to the first phrase, so no comma follows it (v6 §C):
        "Comfort Colors® Funny Nurse Shirt, ...", not "Comfort Colors®, Funny ...".
        """
        listing.title = join_prefix(self._title_prefix, listing.title)
        return listing

    def build_params(self, analysis: VisionAnalysis, sku: str | None = None) -> dict[str, Any]:
        """Request body for this call — reusable for the Batch API later."""
        return self._client.build_params(
            system=self._template.system,
            content_blocks=self._content_blocks(analysis, sku),
            schema=CONTENT_SCHEMA,
            max_tokens=self._max_tokens,
        )

    def _correction_block(
        self, previous: GeneratedListing, errors: list[str]
    ) -> dict[str, Any]:
        """A user turn that tells the model exactly what to fix on the retry.

        Blind retries usually reproduce the same violation, so we quote the
        rejected output back and list every validation error verbatim.
        """
        bullets = "\n".join(f"- {e}" for e in errors)
        text = (
            "Your previous response was REJECTED by validation. Fix every problem "
            "below and return the corrected listing as JSON again — keep what was "
            "already valid, change only what these errors require:\n"
            f"{bullets}\n\n"
            "Your previous (rejected) output was:\n"
            f"- title ({len(previous.title)} chars): {previous.title}\n"
            f"- tags ({len(previous.tags)}): {', '.join(previous.tags)}\n"
            f"- description: {previous.description}"
        )
        return {"type": "text", "text": text}

    async def _generate_once(
        self,
        analysis: VisionAnalysis,
        sku: str | None,
        *,
        correction: dict[str, Any] | None = None,
        pattern: dict[str, Any] | None = None,
    ) -> tuple[GeneratedListing, Usage]:
        blocks = self._content_blocks(analysis, sku)
        if pattern:
            blocks = [*blocks, self._pattern_block(pattern)]
        if correction is not None:
            blocks = [*blocks, correction]
        result = await self._client.complete_json(
            system=self._template.system,
            content_blocks=blocks,
            schema=CONTENT_SCHEMA,
            max_tokens=self._max_tokens,
        )
        return _to_listing(result.data), result.usage

    async def generate(
        self, analysis: VisionAnalysis, sku: str | None = None, pattern: dict[str, Any] | None = None
    ) -> ContentResult:
        """Generate + validate, regenerating once on failure before giving up.

        The retry prompt carries the previous attempt's validation errors so the
        model knows precisely what to correct.
        """
        usages: list[Usage] = []
        last_errors: list[str] = []
        correction: dict[str, Any] | None = None
        for attempt in range(2):
            listing, usage = await self._generate_once(analysis, sku, correction=correction, pattern=pattern)
            usages.append(usage)
            listing = self._apply_prefix(listing)  # prepend the profile's title prefix
            # One character over is not a reason to throw the listing away (v7 §A3).
            listing.title = fit_title(listing.title)
            errors = validate_listing(listing, self._policy, trademarks=self._trademarks)
            if self._policy is not None and self._policy.describe_not_transcribe:
                errors.extend(copied_text_errors(listing.title, analysis.embedded_text))
                errors.extend(theme_errors(listing, analysis.themes, self._trademarks))
            if pattern and pattern.get("title"):
                errors.extend(example_errors(listing.title, str(pattern["title"])))
            if not errors:
                return ContentResult(listing=listing, usages=usages, attempts=attempt + 1)
            last_errors = errors
            correction = self._correction_block(listing, errors)
        raise ContentValidationError(last_errors, usages)


def _to_listing(data: dict[str, Any]) -> GeneratedListing:
    try:
        return GeneratedListing(
            title=str(data["title"]),
            tags=[str(t) for t in data["tags"]],
            description=str(data["description"]),
        )
    except (KeyError, TypeError) as exc:
        raise LLMError(f"content response missing fields: {exc}") from exc
