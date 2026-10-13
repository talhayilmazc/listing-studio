"""No text the app sends to a person says "beta".

The product is described as invite-only early access. The frontend's pages and
components are checked by frontend/lib/homeCopy.test.ts; this checks every
string the backend can put in front of someone: API messages, the invite
e-mail text an operator copies, alerts. Comments and docstrings are not copy.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.api.admin import invite_message

APP = Path(__file__).resolve().parents[1] / "app"


def _strings(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                docstrings.add(id(first.value))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
    ]


def test_no_backend_text_says_beta() -> None:
    found = [
        f"{path.relative_to(APP)}:{line}: {text[:80]!r}"
        for path in sorted(APP.rglob("*.py"))
        for line, text in _strings(path)
        if "beta" in text.lower()
    ]
    assert found == []


def test_the_invite_message_says_what_replaced_it() -> None:
    message = invite_message("https://listyro.com/register?invite=CODE", None)
    assert "A spot has opened in Listyro's early access" in message
    assert "Listyro is invite-only and free while we onboard sellers in small groups." in message
    assert "beta" not in message.lower()
