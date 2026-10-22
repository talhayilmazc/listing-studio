"""Watching Etsy's OpenAPI document for How it's made fields (pipeline/how_made.py).

Once a week the worker downloads Etsy's published OpenAPI document (a public file on etsy.com,
not an API call: no token, no quota) and lists the fields of createDraftListing (request),
updateListing (request) and getListing (response). Any field whose name looks like production
process, tools or AI (:data:`PATTERN`) that is not one of today's (:data:`KNOWN`) is sent to the
operator once (ALERT_WEBHOOK_URL) with the field names. Nothing changes by itself: turning on
sending is a code change and a setting (docs/etsy-how-its-made.md).

Local development never fetches it (ETSY_CALLS_BLOCKED, app/etsy/guard.py).
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from app.core import alerts
from app.core.config import get_settings
from app.etsy import guard

logger = logging.getLogger(__name__)

OPERATIONS = {"createDraftListing": "request", "updateListing": "request", "getListing": "response"}
#: A field about how an item is produced, the tools used or AI.
PATTERN = re.compile(r"produc(e|tion)|tool|craft|creativ|(^|_)ai(_|$)|artificial|how_?(it_?s_?)?made|made_(with|by|how)"
                     r"|making|maker|machine|handmade|hand_made|process|disclos|generat", re.I)
#: Matching fields Etsy already has (2026-10-22): processing times and production partners.
KNOWN = frozenset({"processing_min", "processing_max", "production_partner_ids", "production_partners"})
#: The same alert is not sent again for this long (seconds).
ALERT_AGAIN_AFTER = 30 * 24 * 3600


def _resolve(spec: dict[str, Any], schema: Any) -> Any:
    seen = 0
    while isinstance(schema, dict) and "$ref" in schema and seen < 20:
        schema = spec.get("components", {}).get("schemas", {}).get(schema["$ref"].rsplit("/", 1)[-1], {})
        seen += 1
    return schema


def _properties(spec: dict[str, Any], schema: Any) -> set[str]:
    schema = _resolve(spec, schema)
    if not isinstance(schema, dict):
        return set()
    out = set((schema.get("properties") or {}).keys())
    for key in ("allOf", "oneOf", "anyOf"):
        for inner in schema.get(key) or []:
            out |= _properties(spec, inner)
    items = schema.get("items")
    if items:
        out |= _properties(spec, items)
    return out


def operation_fields(spec: dict[str, Any]) -> dict[str, set[str]]:
    """operationId -> its field names (request body and parameters, or 2xx response)."""
    found: dict[str, set[str]] = {name: set() for name in OPERATIONS}
    for ops in (spec.get("paths") or {}).values():
        for op in ops.values() if isinstance(ops, dict) else []:
            if not isinstance(op, dict) or op.get("operationId") not in OPERATIONS:
                continue
            name = op["operationId"]
            if OPERATIONS[name] == "request":
                for content in ((op.get("requestBody") or {}).get("content") or {}).values():
                    found[name] |= _properties(spec, content.get("schema"))
                found[name] |= {p["name"] for p in op.get("parameters") or [] if isinstance(p, dict) and "name" in p}
            else:
                for code, resp in (op.get("responses") or {}).items():
                    if str(code).startswith("2"):
                        for content in ((resp or {}).get("content") or {}).values():
                            found[name] |= _properties(spec, content.get("schema"))
    return found


def new_how_made_fields(spec: dict[str, Any]) -> dict[str, list[str]]:
    """operationId -> fields that look like How it's made and are new; empty when none."""
    out: dict[str, list[str]] = {}
    for name, fields in operation_fields(spec).items():
        hits = sorted(f for f in fields if PATTERN.search(f) and f not in KNOWN)
        if hits:
            out[name] = hits
    return out


def alert_text(found: dict[str, list[str]]) -> str:
    parts = "; ".join(f"{op}: {', '.join(fields)}" for op, fields in found.items())
    return (f"Listyro: Etsy's OpenAPI document now has fields that may be How it's made (production, tools, AI): {parts}. "
            "Nothing was changed. See docs/etsy-how-its-made.md to turn on sending.")


async def check_etsy_spec(ctx: dict[str, Any], fetch: Any = None) -> str:
    """arq cron (weekly): download the document, look for new fields, tell the operator once."""
    settings = get_settings()
    try:
        guard.check(f"GET {settings.etsy_openapi_url}")
        if fetch is not None:
            spec = await fetch(settings.etsy_openapi_url)
        else:
            async with httpx.AsyncClient(timeout=60.0) as http:
                resp = await http.get(settings.etsy_openapi_url)
                resp.raise_for_status()
                spec = resp.json()
    except Exception as exc:  # noqa: BLE001 - next week's run tries again
        logger.warning("etsy spec check: could not read the document: %s", type(exc).__name__)
        return "unreadable"
    found = new_how_made_fields(spec)
    if not found:
        return "nothing-new"
    key = "etsy-spec-alert:" + "|".join(f"{op}={','.join(f)}" for op, f in sorted(found.items()))
    redis = ctx.get("redis")
    if redis is not None and not await redis.set(key, "1", nx=True, ex=ALERT_AGAIN_AFTER):
        return "already-told"
    await alerts.send(alert_text(found))
    logger.warning("etsy spec check: %s", found)
    return "alerted"
