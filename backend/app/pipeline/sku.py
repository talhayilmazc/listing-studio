"""Extract a SKU from an uploaded file's name using configurable regex rules.

Rules are tried in order; the first whose ``sku`` named group matches wins. The
default set handles the common seller conventions (``SKU123_front.png``,
``ABC-001-back.jpg``, ``TShirt-Red_2.jpg``, bare ``DESIGN9.png``), but callers
can pass their own rules for a different naming scheme.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class SkuRule:
    """A single regex rule. ``pattern`` should contain a group named ``sku``.

    If it has no ``sku`` group, the whole match (group 0) is used.
    """

    pattern: str
    flags: int = re.IGNORECASE

    def compiled(self) -> re.Pattern[str]:
        return re.compile(self.pattern, self.flags)


# Known "view"/angle words that separate a SKU from a descriptor.
_VIEWS = "front|back|main|side|top|bottom|detail|closeup|angle|thumb|hero|left|right"

DEFAULT_SKU_RULES: tuple[SkuRule, ...] = (
    # "<sku>_front", "<sku>-back", ...  (SKU is everything before the view word)
    SkuRule(rf"^(?P<sku>.+?)[-_](?:{_VIEWS})\b"),
    # "<sku>_2", "<sku>-03"  (trailing 1-3 digit index)
    SkuRule(r"^(?P<sku>.+?)[-_]\d{1,3}$"),
    # Whole stem is a clean single token, e.g. "DESIGN9".
    SkuRule(r"^(?P<sku>[A-Za-z0-9][A-Za-z0-9\-_]*)$"),
)


class SkuParser:
    def __init__(
        self,
        rules: Sequence[SkuRule | str] | None = None,
        *,
        normalize: bool = True,
        strip_extension: bool = True,
    ) -> None:
        source = rules if rules is not None else DEFAULT_SKU_RULES
        self._rules: list[SkuRule] = [
            rule if isinstance(rule, SkuRule) else SkuRule(rule) for rule in source
        ]
        self._normalize = normalize
        self._strip_extension = strip_extension

    def parse(self, filename: str) -> str | None:
        """Return the SKU for ``filename`` or ``None`` if no rule matches."""
        name = filename.strip()
        if self._strip_extension:
            name = os.path.splitext(name)[0]
        for rule in self._rules:
            match = rule.compiled().search(name)
            if match is None:
                continue
            sku = match.groupdict().get("sku") or match.group(0)
            if sku:
                return self._apply_normalize(sku)
        return None

    def _apply_normalize(self, sku: str) -> str:
        sku = sku.strip()
        return sku.upper() if self._normalize else sku
