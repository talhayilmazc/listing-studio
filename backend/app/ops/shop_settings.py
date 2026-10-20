"""``python -m app.cli resolve-shop-settings [--apply] [--email]``: every account's profile
links filled from the shop setting map, and the links still lacking a shipping profile,
return policy or processing profile resolved again (``workers/links.py::resolve_all``).

The worker does this once by itself after the deploy that added the map
(``resolve_shop_settings_once``); this runs it by hand (again, or for one account).
Without ``--apply`` nothing is written and nothing is queued. Filling from the map makes
no Etsy request; each profile queued for resolving reads each shop's lists at most once
an hour, through the queue, the gate and the app's budget (upkeep).
"""

from __future__ import annotations

from typing import Any

from app.workers.links import resolve_all


async def run(sm: Any, *, apply: bool, email: str | None = None) -> str:
    async with sm() as session:
        result = await resolve_all(session, apply=apply, email=email)
    queued = 0
    if apply and result["profiles"]:
        from app.api.deps import get_enqueuer

        enqueuer = get_enqueuer()
        for profile_id in result["profiles"]:
            await enqueuer.enqueue("link_profile", str(profile_id))
            queued += 1
    mode = "APPLIED" if apply else "dry run, nothing was changed (add --apply)"
    return "\n".join([
        f"resolve-shop-settings: {mode}",
        f"  accounts            {result['accounts']:>6}",
        f"  links filled        {result['links_filled']:>6}  (from the map; no Etsy request)",
        f"  links to resolve    {result['links_to_resolve']:>6}  (still missing a mapped setting)",
        f"  profiles queued     {queued if apply else len(result['profiles']):>6}  (link_profile, one each)",
    ])
