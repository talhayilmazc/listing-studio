"""``python -m app.cli draft-failures <email> [--since YYYY-MM-DD]``: one account's failed
and half-made drafts, read only, for diagnosis on the server.

For each draft (by listing and shop): the shop and the Etsy listing id (or "not created"),
the step that failed, Etsy's answer, what the request for that step carried and whether a
newer draft of the same design in the same shop succeeded later (superseded).

* Etsy's answer: the status, step and Etsy's one-line reason kept with the job since
  2026-10-18 (etsy/errors.py ``record``); for older failures only the stored message
  exists, and it is printed as stored and as the seller now sees it.
* What was sent: field names and the kind and length of each value (errors.shape_of),
  never the seller's words; recorded since 2026-10-18. For an older personalization
  failure, the question as it would be sent now is shown in the same form, with what
  Etsy's rules (pipeline/personalization.py) say about it.

No token is read or printed (tokens are not selected), no buyer data exists in these
tables, and nothing is sent to Etsy. Only SELECTs: on Postgres the transaction is READ
ONLY and rolled back at the end. Only the named account's rows.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logsafety import redact
from app.db.models import (
    Asset,
    DraftAttempt,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    ListingProfile,
    ListingPublication,
    Tenant,
)
from app.etsy.errors import shape_of
from app.pipeline import personalization, unfinished


async def report(sm: async_sessionmaker, email: str, since: date | None = None) -> str:
    async with sm() as session:
        if session.bind.dialect.name == "postgresql":
            await session.execute(text("SET TRANSACTION READ ONLY"))
        try:
            return await _report(session, email, since)
        finally:
            await session.rollback()


def _aware(at: datetime | None) -> datetime | None:
    return at if at is None or at.tzinfo else at.replace(tzinfo=timezone.utc)


def _when(at: datetime | None) -> str:
    if at is None:
        return "?"
    at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    return at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _shape(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True) if value is not None else "not recorded"


async def _report(session: AsyncSession, email: str, since: date | None) -> str:
    tenant = (await session.execute(select(Tenant).where(Tenant.email == email.strip().lower()))).scalar_one_or_none()
    if tenant is None:
        return f"no account with the address {email}"
    start = datetime(since.year, since.month, since.day, tzinfo=timezone.utc) if since else None
    shops = {s.id: s.shop_name or f"shop {s.shop_id}" for s in (await session.execute(
        select(EtsyConnection.id, EtsyConnection.shop_name, EtsyConnection.shop_id).where(EtsyConnection.tenant_id == tenant.id)
    )).all()}

    # The latest failed draft job per listing and shop.
    failed: dict[tuple[str, uuid.UUID], Job] = {}
    query = select(Job).where(Job.tenant_id == tenant.id, Job.type == JobType.create_draft, Job.status == JobStatus.failed)
    if start is not None:
        query = query.where(Job.created_at >= start)
    for job in (await session.execute(query.order_by(Job.created_at))).scalars():
        if job.connection_id is not None:
            failed[(str((job.payload or {}).get("content_id")), job.connection_id)] = job
    attempts = {(str(a.content_id), a.connection_id): a for a in (await session.execute(
        select(DraftAttempt).where(DraftAttempt.tenant_id == tenant.id)
    )).scalars() if a.etsy_listing_id is not None and (start is None or a.created_at is None or _aware(a.created_at) >= start)}
    replaced = {(str(h.content_id), h.connection_id): h.replaced_by
                for h in await unfinished.half_made(session, tenant.id, with_blocks=False)}
    published = {(str(p.content_id), p.connection_id) for p in (await session.execute(
        select(ListingPublication.content_id, ListingPublication.connection_id).where(ListingPublication.tenant_id == tenant.id)
    )).all()}

    keys = sorted(set(failed) | set(attempts), key=lambda k: _when((failed.get(k) or attempts.get(k)).created_at))
    lines = [f"Account {tenant.email} ({str(tenant.id)[:8]}): failed and half-made drafts"
             + (f" since {since.isoformat()}" if since else ""), ""]
    if not keys:
        lines.append("None.")
        return "\n".join(lines)
    for key in keys:
        content_id, shop_id = key
        job, attempt = failed.get(key), attempts.get(key)
        shop = shops.get(shop_id, "a disconnected shop")
        content = await session.get(GeneratedContent, uuid.UUID(content_id)) if content_id not in ("None", "") else None
        if content is not None and content.tenant_id != tenant.id:
            continue
        listing = f"listing {attempt.etsy_listing_id}" if attempt is not None else "not created on Etsy"
        finished = key in published
        lines.append(f"- {shop} · {listing} · failed {_when(job.finished_at if job else None)}"
                     + (" · finished since" if finished and attempt is None else ""))
        if content is not None:
            group = (await session.execute(select(Asset.group_key).where(Asset.id == content.asset_id))).scalar()
            lines.append(f"  design: batch {str(content.batch_id)[:8] if content.batch_id else '-'}, group {group or '-'}")
        record = (job.payload or {}).get("etsy_error") if job else None
        step = (record or {}).get("step") or (unfinished.step_of(job.last_error) if job else None)
        lines.append(f"  step: {step or 'not recorded'}")
        if record:
            lines.append(f"  Etsy answered: {record.get('status')} {record.get('method') or ''} {record.get('path') or ''}".rstrip())
            lines.append(f"  Etsy's reason: {redact(record.get('message') or 'none given')}")
            lines.append(f"  sent: {_shape(record.get('sent'))}")
        elif job is not None:
            stored = redact(job.last_error or "")
            lines.append(f"  stored message (before Etsy's answer was kept): {stored or '-'}")
            now = unfinished.plain_error(job.last_error, shop)
            if now and now != job.last_error:
                lines.append(f"  shown to the seller now: {redact(now)}")
            lines.append("  sent: not recorded (failed before request shapes were kept)")
        if step and "personalization" in step and content is not None:
            profile = await session.get(ListingProfile, content.listing_profile_id) if content.listing_profile_id else None
            setting, source = personalization.for_listing(
                content.personalization, profile.personalization if profile else None,
                profile.cached_payload if profile else None)
            if setting and setting.get("enabled"):
                body = {"personalization_questions": personalization.questions_for(setting)}
                lines.append(f"  personalization as it would be sent now ({source}): {_shape(shape_of(body))}")
                problems = personalization.etsy_problems(setting)
                lines.append("  Etsy's rules: " + ("; ".join(problems) if problems else "it meets them"))
            else:
                lines.append(f"  personalization now: off ({source})")
        newer = replaced.get(key)
        if newer:
            lines.append(f"  superseded: yes, a newer draft of the same design succeeded here (listing {newer})")
        elif finished:
            lines.append("  superseded: finished since (this listing's own draft succeeded)")
        else:
            lines.append("  superseded: no")
        lines.append("")
    return "\n".join(lines)
