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

from app.pipeline.llm import LLMClient, LLMError, Usage
from app.pipeline.templates import PromptTemplate, load_template
from app.pipeline.vision import VisionAnalysis

# Etsy structural limits.
REQUIRED_TAG_COUNT = 13
MIN_TITLE_LENGTH = 110
MAX_TITLE_LENGTH = 140
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

_POLICIES: dict[str, ContentPolicy] = {
    "apparel": ContentPolicy(_APPAREL_FORBIDDEN, _APPAREL_TYPES),
    # Digital sellers may legitimately use "digital download", "SVG", etc.
    "digital_products": ContentPolicy(),
}


def policy_for(content_template: str) -> ContentPolicy:
    """Return the content policy for a profile's ``content_template`` (default: none)."""
    return _POLICIES.get(content_template, ContentPolicy())


def _has_term(text: str, term: str) -> bool:
    """Whole-word (case-insensitive) match, so 'png' won't fire inside 'opening'."""
    return re.search(rf"\b{re.escape(term)}\b", text, re.IGNORECASE) is not None

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
    listing: GeneratedListing, policy: ContentPolicy | None = None
) -> list[str]:
    """Return validation errors (empty if valid).

    Structural Etsy limits always apply; ``policy`` adds product-type rules
    (forbidden format words, required product-type wording) scoped to the profile.
    """
    errors: list[str] = []

    title = listing.title.strip()
    n = len(listing.title)
    if not title:
        errors.append("title is empty")
    elif n < MIN_TITLE_LENGTH:
        errors.append(
            f"title must be at least {MIN_TITLE_LENGTH} characters "
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

    if policy.required_type_terms:
        options = ", ".join(policy.required_type_terms)
        if not any(_has_term(title, term) for term in policy.required_type_terms):
            errors.append(f"title must name the product type (one of: {options})")
        if not any(_has_term(tag, term) for tag in tags for term in policy.required_type_terms):
            errors.append(f"at least one tag must name the product type (e.g. {options})")

    return errors


class ContentGenerator(Protocol):
    async def generate(self, analysis: VisionAnalysis, sku: str | None = None) -> ContentResult: ...


class AnthropicContentGenerator:
    def __init__(
        self,
        client: LLMClient,
        template: PromptTemplate | None = None,
        *,
        max_tokens: int = 1024,
        policy: ContentPolicy | None = None,
    ) -> None:
        self._client = client
        self._template = template or load_template("content/digital_products")
        self._max_tokens = max_tokens
        self._policy = policy

    def _content_blocks(self, analysis: VisionAnalysis, sku: str | None) -> list[dict[str, Any]]:
        text = self._template.render_user(
            {
                "theme": analysis.theme,
                "embedded_text": analysis.embedded_text or "(none)",
                "style": analysis.style,
                "colors": ", ".join(analysis.colors),
                "target_audience": analysis.target_audience,
                "product_type_hints": ", ".join(analysis.product_type_hints),
                "sku": sku or "(none)",
            }
        )
        return [{"type": "text", "text": text}]

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
    ) -> tuple[GeneratedListing, Usage]:
        blocks = self._content_blocks(analysis, sku)
        if correction is not None:
            blocks = [*blocks, correction]
        result = await self._client.complete_json(
            system=self._template.system,
            content_blocks=blocks,
            schema=CONTENT_SCHEMA,
            max_tokens=self._max_tokens,
        )
        return _to_listing(result.data), result.usage

    async def generate(self, analysis: VisionAnalysis, sku: str | None = None) -> ContentResult:
        """Generate + validate, regenerating once on failure before giving up.

        The retry prompt carries the previous attempt's validation errors so the
        model knows precisely what to correct.
        """
        usages: list[Usage] = []
        last_errors: list[str] = []
        correction: dict[str, Any] | None = None
        for attempt in range(2):
            listing, usage = await self._generate_once(analysis, sku, correction=correction)
            usages.append(usage)
            errors = validate_listing(listing, self._policy)
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
