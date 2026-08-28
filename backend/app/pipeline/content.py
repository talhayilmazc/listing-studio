"""Listing content generation: title, exactly 13 tags, and description.

Validates the model output against Etsy's structural limits before it can be
persisted. Structured outputs can't express "exactly 13 items" or per-item length
limits, so these are enforced here: an invalid result is regenerated **once**,
then reported as failed.
"""

from __future__ import annotations

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


def validate_listing(listing: GeneratedListing) -> list[str]:
    """Return a list of validation errors (empty if the listing is valid)."""
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
    ) -> None:
        self._client = client
        self._template = template or load_template("content/digital_products")
        self._max_tokens = max_tokens

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
            errors = validate_listing(listing)
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
