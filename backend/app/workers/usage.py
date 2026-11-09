"""The listing counter's daily check (fix/usage-and-admin-publishing; core/usage.py).

Compares ``usage_event`` with the publication records. A listing with a draft and no usage row, or a
usage row with no successful draft (its shop still connected), is told to ALERT_WEBHOOK_URL with counts
and ids only; nothing is changed here (``python -m app.cli usage-reconcile --apply`` adds missing rows).
"""

from __future__ import annotations

from typing import Any

from app.core import alerts, usage

MAX_IDS = 20


def alert_text(found: usage.Reconciled) -> str:
    def ids(pairs: list) -> str:
        shown = ", ".join(f"{t}/{k}" for t, k in pairs[:MAX_IDS])
        return shown + (f" and {len(pairs) - MAX_IDS} more" if len(pairs) > MAX_IDS else "")

    lines = ["[Listyro] The listing counter and the drafts disagree."]
    if found.missing:
        lines.append(f"Drafted, not counted: {len(found.missing)} ({ids(found.missing)})")
    if found.orphans:
        lines.append(f"Counted, no draft: {len(found.orphans)} ({ids(found.orphans)})")
    lines.append("Check: python -m app.cli usage-reconcile --all (read only; --apply adds the missing rows).")
    return "\n".join(lines)


async def reconcile_usage(ctx: dict[str, Any]) -> str:
    async with ctx["sessionmaker"]() as session:
        found = await usage.reconcile(session)
        await session.rollback()
    if found.clean:
        return "clean"
    await alerts.send(alert_text(found))
    return f"missing:{len(found.missing)} orphans:{len(found.orphans)}"
