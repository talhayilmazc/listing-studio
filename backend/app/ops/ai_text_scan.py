"""``python -m app.cli scan-ai-text [--fix-unsent]``: listings whose AI-written text has stray characters
(invisible characters, leftover code characters such as ``}]``; pipeline/ai_text.py), per account.

Counts and ids only, never text. Split in two:

* **not yet sent**: no draft or live listing on Etsy. ``--fix-unsent`` cleans these in our database only
  (title, tags, description, and any shop's own text), and audits each (``listing.ai_text_cleaned``).
* **already sent**: on Etsy as a draft or live listing. Never changed here. Each shows in the seller's
  Listing health as "Fix now: description has stray characters" with the cleaned text to approve
  (pipeline/listing_fixes.py::stray, kind ``clean_text``).

Without ``--fix-unsent`` it is read only: SELECTs, nothing written, no Etsy call.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import audit
from app.db.models import GeneratedContent, ListingPublication
from app.pipeline import ai_text

#: The fields of a listing (and of a shop's own text) the scan reads, and whether each keeps line breaks.
FIELDS = (("title", False), ("tags", False), ("description", True))


def bad_fields(row: dict[str, Any]) -> list[str]:
    """The fields of ``row`` (a listing's or a shop's own text) that have stray characters."""
    out = []
    for name, lines in FIELDS:
        value = row.get(name)
        if name == "tags":
            if any(ai_text.junk(str(t)) for t in value or []):
                out.append(name)
        elif value and ai_text.junk(str(value), lines=lines):
            out.append(name)
    return out


def cleaned(row: dict[str, Any]) -> dict[str, Any]:
    """``row``'s text fields, each through the sanitiser (the same rule as everything the model writes)."""
    out: dict[str, Any] = {}
    for name, lines in FIELDS:
        if name not in row or row[name] is None:
            continue
        out[name] = ai_text.clean_tags(list(row[name])) if name == "tags" else ai_text.clean(str(row[name]), lines=lines)
    return out


@dataclass
class Found:
    content_id: uuid.UUID
    fields: list[str]
    #: Etsy listing ids of its drafts or live listings; empty: not sent yet.
    on_etsy: list[int] = field(default_factory=list)


async def scan(session: AsyncSession) -> dict[uuid.UUID, list[Found]]:
    """Every AI-written listing with stray characters, per account. Hand-written text is the seller's own: left out."""
    sent: dict[uuid.UUID, list[int]] = defaultdict(list)
    for content_id, listing_id in (await session.execute(
        select(ListingPublication.content_id, ListingPublication.etsy_listing_id)
        .where(ListingPublication.content_id.is_not(None), ListingPublication.state != "deleted_on_etsy")
    )).all():
        sent[content_id].append(int(listing_id))
    out: dict[uuid.UUID, list[Found]] = defaultdict(list)
    rows = await session.execute(select(GeneratedContent).where(GeneratedContent.source != "manual"))
    for content in rows.scalars():
        own = {"title": content.title, "tags": content.tags, "description": content.description}
        fields = bad_fields(own)
        for shop, override in (content.shop_overrides or {}).items():
            fields += [f"{name} for one shop" for name in bad_fields(override or {})]
        if fields:
            out[content.tenant_id].append(Found(content.id, list(dict.fromkeys(fields)), sorted(sent.get(content.id, []))))
    return out


async def fix_unsent(session: AsyncSession, found: dict[uuid.UUID, list[Found]]) -> int:
    """Clean the not-yet-sent listings in our database. What is on Etsy is never touched here."""
    fixed = 0
    for tenant_id, items in found.items():
        for item in items:
            if item.on_etsy:
                continue
            content = await session.get(GeneratedContent, item.content_id)
            if content is None:
                continue
            for name, value in cleaned({"title": content.title, "tags": content.tags, "description": content.description}).items():
                setattr(content, name, value)
            if content.shop_overrides:
                content.shop_overrides = {
                    shop: {**override, **cleaned(override or {})} for shop, override in content.shop_overrides.items()
                }
            audit.record(session, "listing.ai_text_cleaned", actor=None, target_tenant_id=tenant_id,
                         content_id=str(content.id), fields=item.fields, trigger="operator")
            fixed += 1
    await session.commit()
    return fixed


def report(found: dict[uuid.UUID, list[Found]], fixed: int | None = None) -> str:
    lines = ["AI-written listings with stray characters (counts and ids; no text):"]
    if not found:
        lines.append("  none.")
    unsent_total = sent_total = 0
    for tenant_id, items in sorted(found.items(), key=lambda kv: str(kv[0])):
        unsent = [i for i in items if not i.on_etsy]
        sent = [i for i in items if i.on_etsy]
        unsent_total += len(unsent)
        sent_total += len(sent)
        lines.append(f"account {tenant_id}: not yet sent {len(unsent)}, already sent to Etsy {len(sent)}")
        for item in unsent:
            lines.append(f"  not yet sent  listing {item.content_id}  ({', '.join(item.fields)})")
        for item in sent:
            on = ", ".join(str(i) for i in item.on_etsy)
            lines.append(f"  sent         listing {item.content_id}  Etsy {on}  ({', '.join(item.fields)})")
    lines.append(f"Total: not yet sent {unsent_total}, already sent {sent_total}.")
    if sent_total:
        lines.append("Already sent: never changed here; each is a \"Fix now\" item in that seller's Listing health, with the cleaned text to approve.")
    if fixed is not None:
        lines.append(f"Cleaned {fixed} not-yet-sent listing(s) in the database. Nothing was sent to Etsy.")
    elif unsent_total:
        lines.append("Run again with --fix-unsent to clean the not-yet-sent ones in the database (nothing goes to Etsy).")
    return "\n".join(lines) + "\n"


async def run(sm: async_sessionmaker, *, fix: bool) -> str:
    async with sm() as session:
        found = await scan(session)
        fixed = await fix_unsent(session, found) if fix else None
        if fixed is not None:
            found = await scan(session)  # what is left: the sent ones
    return report(found, fixed)
