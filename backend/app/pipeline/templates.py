"""Prompt templates loaded from the ``prompts/`` directory.

Templates live as plain files so they can be swapped without code changes. Each
template is a directory containing ``system.md`` (stable, cacheable instructions)
and ``user.md`` (the per-call turn, with ``$placeholder`` variables filled at
render time).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from string import Template

_PROMPTS_DIR = Path(__file__).parent / "prompts"


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    system: str
    user: str

    def render_user(self, variables: dict[str, str]) -> str:
        """Fill ``$placeholder`` variables in the user turn (missing keys kept)."""
        return Template(self.user).safe_substitute(variables)


def load_template(path: str, base_dir: Path | None = None) -> PromptTemplate:
    """Load ``<base>/<path>/{system.md,user.md}`` (e.g. ``content/digital_products``)."""
    directory = (base_dir or _PROMPTS_DIR) / path
    system = (directory / "system.md").read_text(encoding="utf-8").strip()
    user = (directory / "user.md").read_text(encoding="utf-8").strip()
    return PromptTemplate(name=path, system=system, user=user)
