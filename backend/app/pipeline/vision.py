"""Vision analysis of a processed design derivative.

A provider-agnostic :class:`VisionAnalyzer` protocol with an Anthropic
implementation. The analyzer always receives the **downscaled derivative**, never
the original upload — the caller passes the processed bytes.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Protocol

from app.pipeline.llm import LLMClient, LLMError, Usage
from app.pipeline.templates import PromptTemplate, load_template

VISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "theme": {"type": "string"},
        "embedded_text": {"type": "string"},
        "style": {"type": "string"},
        "colors": {"type": "array", "items": {"type": "string"}},
        "target_audience": {"type": "string"},
        "product_type_hints": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "theme",
        "embedded_text",
        "style",
        "colors",
        "target_audience",
        "product_type_hints",
    ],
    "additionalProperties": False,
}


@dataclass
class VisionAnalysis:
    theme: str
    embedded_text: str
    style: str
    colors: list[str]
    target_audience: str
    product_type_hints: list[str]


@dataclass
class VisionResult:
    analysis: VisionAnalysis
    usage: Usage


class VisionAnalyzer(Protocol):
    async def analyze(self, image_data: bytes, media_type: str) -> VisionResult: ...


class AnthropicVisionAnalyzer:
    def __init__(self, client: LLMClient, template: PromptTemplate | None = None) -> None:
        self._client = client
        self._template = template or load_template("vision/default")

    def _content_blocks(self, image_data: bytes, media_type: str) -> list[dict[str, Any]]:
        # Image last so it is the only volatile part after the cached prefix.
        encoded = base64.standard_b64encode(image_data).decode("ascii")
        return [
            {"type": "text", "text": self._template.user},
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": encoded},
            },
        ]

    def build_params(self, image_data: bytes, media_type: str) -> dict[str, Any]:
        """Request body for this call — reusable for the Batch API later."""
        return self._client.build_params(
            system=self._template.system,
            content_blocks=self._content_blocks(image_data, media_type),
            schema=VISION_SCHEMA,
        )

    async def analyze(self, image_data: bytes, media_type: str) -> VisionResult:
        result = await self._client.complete_json(
            system=self._template.system,
            content_blocks=self._content_blocks(image_data, media_type),
            schema=VISION_SCHEMA,
        )
        return VisionResult(analysis=_to_analysis(result.data), usage=result.usage)


def _to_analysis(data: dict[str, Any]) -> VisionAnalysis:
    try:
        return VisionAnalysis(
            theme=str(data["theme"]),
            embedded_text=str(data["embedded_text"]),
            style=str(data["style"]),
            colors=[str(c) for c in data["colors"]],
            target_audience=str(data["target_audience"]),
            product_type_hints=[str(h) for h in data["product_type_hints"]],
        )
    except (KeyError, TypeError) as exc:
        raise LLMError(f"vision response missing fields: {exc}") from exc
