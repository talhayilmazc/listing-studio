"""A shop's own sections, for the review card's Section control (pipeline/item_options.py).

* ``sync_shop_sections``: one request (getShopSections) through the queue, kept
  on the shop 24 hours (other Etsy content). Upkeep: it reads the seller's own
  shop for the app, so it does not count against the seller's daily limit.
* ``create_shop_section``: the seller's own work, run only after they confirmed
  "Create section '<name>' in <shop>" (createShopSection, scope ``shops_w``). It
  reads the sections first: one that appeared meanwhile is used, never made twice.
  The listings that asked for it get it as their section in that shop.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core import limits
from app.core.config import get_settings
from app.db.models import GeneratedContent, Tenant
from app.pipeline import item_options
from app.workers.profiles import (
    _active_shop,
    _build_client,
    _connection_service,
    _resolve_shop_id,
    _run_gated,
    _shop_owner,
)


def sections_from(response: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"id": int(s["shop_section_id"]), "title": str(s.get("title") or "")}
        for s in response.get("results") or []
        if s.get("shop_section_id") is not None
    ]


async def sync_shop_sections(ctx: dict[str, Any], connection_id: str) -> str:
    tenant_id = await _shop_owner(ctx, connection_id)
    if tenant_id is None:
        return "no-connection"
    return await _run_gated(ctx, "sync_shop_sections", connection_id, tenant_id, lambda: _read(ctx, connection_id))


async def _read(ctx: dict[str, Any], connection_id: str, *, create: str | None = None) -> str:
    settings = get_settings()
    service = _connection_service(settings)
    async with ctx["sessionmaker"]() as session:
        connection = await _active_shop(session, uuid.UUID(connection_id))
        if connection is None:
            return "no-connection"
        tenant = await session.get(Tenant, connection.tenant_id)
        token = await service.get_valid_access_token(session, connection)
        async with httpx.AsyncClient(timeout=30.0) as http:
            client = _build_client(ctx, http, settings, shop=connection.id)
            kw = {
                "access_token": token,
                "tenant_id": connection.tenant_id,
                "tenant_limit": limits.ceiling_limit(tenant) if tenant else None,
            }
            shop_id = await _resolve_shop_id(session, client, connection, kw)
            if ctx.get("redis") is not None and create is not None:
                # A fresh list, not the client's cached one: the section may exist by now.
                from app.etsy.api import SECTIONS_KEY

                await ctx["redis"].delete(SECTIONS_KEY.format(shop_id=shop_id))
            sections = sections_from(await client.get_shop_sections(shop_id, **kw))
            made = None
            if create is not None:
                made = next((s for s in sections if s["title"].strip().lower() == create.strip().lower()), None)
                if made is None:
                    answer = await client.create_shop_section(shop_id, title=create, **kw)
                    made = {"id": int(answer["shop_section_id"]), "title": str(answer.get("title") or create)}
                    sections.append(made)
        connection.sections = sections
        connection.sections_at = datetime.now(timezone.utc)
        await session.commit()
        return f"sections:{len(sections)}" + (f":made:{made['id']}" if made else "")


def create_spec(connection_id: uuid.UUID | str, title: str, content_ids: list[uuid.UUID | str]) -> str:
    """The one argument ``create_shop_section`` takes (the gate re-queues a paused job
    with a single argument)."""
    return json.dumps({"connection_id": str(connection_id), "title": title, "content_ids": [str(c) for c in content_ids]})


async def create_shop_section(ctx: dict[str, Any], spec: str) -> str:
    wanted = json.loads(spec)
    connection_id, title = str(wanted["connection_id"]), str(wanted["title"])
    content_ids = [str(c) for c in wanted.get("content_ids") or []]
    tenant_id = await _shop_owner(ctx, connection_id)
    if tenant_id is None:
        return "no-connection"

    async def body() -> str:
        result = await _read(ctx, connection_id, create=title)
        if ":made:" in result and content_ids:
            section_id = int(result.rsplit(":", 1)[1])
            async with ctx["sessionmaker"]() as session:
                for cid in content_ids:
                    content = await session.get(GeneratedContent, uuid.UUID(cid))
                    if content is None or content.tenant_id != tenant_id:
                        continue
                    options = dict(content.item_options or {})
                    sections = dict(options.get(item_options.SECTIONS) or {})
                    sections[connection_id] = {"id": section_id, "title": title}
                    options[item_options.SECTIONS] = sections
                    content.item_options = options
                await session.commit()
        return result

    return await _run_gated(ctx, "create_shop_section", spec, tenant_id, body)
