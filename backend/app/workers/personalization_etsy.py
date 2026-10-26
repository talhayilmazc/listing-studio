"""Personalization written to a listing already on Etsy, only when the seller asks (2026-10-26).

A draft made while its listing's personalization was off has no personalization box on
Etsy. When the seller turns it on for such a listing (the review card, the review page's
"Turn on personalization for these N listings", or the profile's "Also apply to N existing
drafts"), one job per listing and shop writes it with updateListingPersonalization, on the
draft or, when the seller already published it, on the live listing, and reads it back:

* the setting sent is the listing's own, else its profile's, as it is when the job runs;
* Etsy's rules are checked first (``personalization.etsy_problems``): a question Etsy would
  refuse is never sent, and the card offers the one-click fix;
* Etsy's answer (or getListingPersonalization) must hold the question, whether it is
  required, the character limit and the instructions (``personalization.differs``);
* a listing Etsy no longer has is marked "deleted on Etsy" (``etsy/reconcile.py``).

The outcome is kept on the record (``listing_publication.personalization_sync``) and shown on
the card. The seller's own work: it counts against their daily limit (about 2 requests).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core import audit, limits
from app.core.config import get_settings
from app.db.models import GeneratedContent, ListingProfile, ListingPublication, Tenant
from app.etsy import reconcile
from app.etsy.errors import EtsyClientError, EtsyError, explain
from app.pipeline import personalization as P
from app.workers.profiles import _active_shop, _build_client, _connection_service, _resolve_shop_id, _run_gated

#: updateListingPersonalization, and getListingPersonalization when its answer does not say.
REQUESTS = 2

QUEUED = "queued"
WRITTEN = "written"
FAILED = "failed"


def spec_for(publication_id: uuid.UUID | str) -> str:
    return json.dumps({"publication_id": str(publication_id)})


def mark(publication: ListingPublication, state: str, message: str | None = None, **extra: Any) -> None:
    publication.personalization_sync = {"state": state, "at": datetime.now(timezone.utc).isoformat(),
                                        "message": message, **extra}


async def setting_for(session, content: GeneratedContent) -> dict[str, Any] | None:  # noqa: ANN001
    """What this listing's personalization is now: its own, else its profile's."""
    profile = await session.get(ListingProfile, content.listing_profile_id) if content.listing_profile_id else None
    setting, _ = P.for_listing(content.personalization, profile.personalization if profile else None,
                               profile.cached_payload if profile else None)
    return setting


async def apply_personalization_on_etsy(ctx: dict[str, Any], spec: str) -> str:
    wanted = json.loads(spec)
    async with ctx["sessionmaker"]() as session:
        publication = await session.get(ListingPublication, uuid.UUID(wanted["publication_id"]))
        if publication is None:
            return "no-publication"
        tenant_id = publication.tenant_id
    return await _run_gated(ctx, "apply_personalization_on_etsy", spec, tenant_id, lambda: _apply(ctx, wanted))


async def _apply(ctx: dict[str, Any], wanted: dict[str, Any]) -> str:
    settings = get_settings()
    async with ctx["sessionmaker"]() as session:
        publication = await session.get(ListingPublication, uuid.UUID(wanted["publication_id"]))
        if publication is None or publication.state == "deleted_on_etsy":
            return "no-publication"
        content = await session.get(GeneratedContent, publication.content_id) if publication.content_id else None
        connection = await _active_shop(session, publication.connection_id)
        if content is None or connection is None or connection.tenant_id != publication.tenant_id:
            mark(publication, FAILED, "The listing or its shop is no longer here, so nothing was written to Etsy.")
            await session.commit()
            return "gone"
        setting = await setting_for(session, content)
        if not setting or not setting.get("enabled"):
            mark(publication, FAILED, "Personalization is off for this listing now, so nothing was written to Etsy.")
            await session.commit()
            return "off"
        problems = P.etsy_problems(setting)
        if problems:
            mark(publication, FAILED, f"Not sent: Etsy would refuse it ({'; '.join(problems)}). "
                 "Use the suggested text on the card, then turn it on again.")
            await session.commit()
            return "refused-here"
        tenant = await session.get(Tenant, publication.tenant_id)
        token = await _connection_service(settings).get_valid_access_token(session, connection)
        kw = {"access_token": token, "tenant_id": publication.tenant_id,
              "tenant_limit": limits.ceiling_limit(tenant) if tenant else None}
        listing_id = int(publication.etsy_listing_id)
        try:
            async with httpx.AsyncClient(timeout=30.0) as http:
                client = _build_client(ctx, http, settings, shop=connection.id)
                shop_id = await _resolve_shop_id(session, client, connection, kw)
                answer = await client.update_listing_personalization(
                    shop_id, listing_id, questions=P.questions_for(setting), **kw)
                back = answer if isinstance(answer, dict) and "personalization_questions" in answer \
                    else await client.get_listing_personalization(listing_id, **kw)
        except EtsyClientError as exc:
            if exc.status_code in (404, 410):
                await reconcile.apply(session, publication, reconcile.GONE)
                await session.commit()
                return "deleted-on-etsy"
            mark(publication, FAILED, f"Etsy refused it: {explain(exc, connection.shop_name)}.")
            await session.commit()
            return "refused"
        except EtsyError as exc:
            mark(publication, FAILED, f"Not finished: {explain(exc, connection.shop_name)}. Try again.")
            await session.commit()
            return "failed"
        except httpx.TransportError:
            mark(publication, FAILED, "Not finished: Etsy did not answer. Try again; it sets the same question, "
                 "so nothing is doubled.")
            await session.commit()
            return "failed"
        lost = P.differs(setting, back)
        if lost:
            mark(publication, FAILED, "Etsy did not keep it as sent (" + ", ".join(lost) + "); check it in Shop Manager.")
            await session.commit()
            return "not-kept"
        mark(publication, WRITTEN, None, question=str(setting.get("question_text") or "")[:P.MAX_QUESTION_TEXT],
             live=publication.state == "active")
        audit.record(session, "listing.personalization_on_etsy", actor=None, target_tenant_id=publication.tenant_id,
                     by="seller", publication_id=str(publication.id), shop_id=str(connection.id),
                     etsy_listing_id=listing_id, live=publication.state == "active")
        await session.commit()
        return "written"
