"""The backend tests' fake Etsy, with what the profile form reads: for the draft browser test.

``tests/test_end_to_end.py::FakeEtsy`` answers every request a draft makes, over HTTP (a transport, so the
app's own client, pacing and quota are the real ones). A profile made by hand in the browser needs a little
more of Etsy, added here and nowhere else:

* a shop's own shipping profiles, return policies, processing profiles and production partners, its currency,
  Etsy's category tree and a category's attributes (the sample lists of ``app/ops/profile_form_demo.py``);
* a listing's attributes (written one by one, read back);
* everything a ``createDraftListing`` carried, kept as sent (``sent``), so a test can compare the request with
  the profile's values. ``on_sent`` is told too (the browser stack keeps it where the test can read it).

Used by ``tests/test_manual_profiles.py`` (the fake itself, in a second) and ``scripts/draft_e2e.py`` (the
stack the browser test runs against, docker-compose.draft-e2e.yml). Synthetic: nothing here is a real shop's.
"""

from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs

import httpx

from app.ops import profile_form_demo as demo
from tests.test_end_to_end import FakeEtsy

#: What every shop of the fake has (a shop with a production partner; ids made up).
LISTS = demo.lists_for(0, False)
_LIST_PATHS = {"shipping-profiles": "shipping", "policies/return": "returns", "readiness-state-definitions": "readiness",
               "production-partners": "partners"}
_ID = re.compile("/[0-9]+")


class BrowserEtsy(FakeEtsy):
    def __init__(self, on_sent: Callable[[dict[str, Any]], Awaitable[None]] | None = None) -> None:
        super().__init__()
        #: Every createDraftListing, as sent: {"shop_id", "form": {field: value}}; with the listing it made.
        self.sent: list[dict[str, Any]] = []
        self._on_sent = on_sent
        #: listing id -> {property id: the body it was written with}
        self.properties: dict[int, dict[int, dict[str, Any]]] = {}
        #: listing id -> the form it was created with (what getListing gives back beyond the base fake's fields).
        self._forms: dict[int, dict[str, str]] = {}

    async def handle(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path.removeprefix("/v3/application")
        if method == "GET" and (m := re.fullmatch(r"/shops/\d+/(shipping-profiles|policies/return|readiness-state-definitions|production-partners)", path)):
            rows = LISTS[_LIST_PATHS[m.group(1)]]
            return self._seen(method, path, httpx.Response(200, json={"count": len(rows), "results": rows}))
        if method == "GET" and (m := re.fullmatch(r"/shops/\d+/shipping-profiles/(\d+)", path)):
            row = next((r for r in LISTS["shipping"] if r["shipping_profile_id"] == int(m.group(1))), None)
            return self._seen(method, path, httpx.Response(200, json=row) if row else httpx.Response(404, json={"error": "not found"}))
        if method == "GET" and (m := re.fullmatch(r"/shops/(\d+)", path)):
            return self._seen(method, path, httpx.Response(200, json={
                "shop_id": int(m.group(1)), "shop_name": "Pine Ridge Tees", "currency_code": LISTS["currency"]}))
        if method == "GET" and path == "/seller-taxonomy/nodes":
            return self._seen(method, path, httpx.Response(200, json=demo.NODES))
        if method == "GET" and re.fullmatch(r"/seller-taxonomy/nodes/\d+/properties", path):
            return self._seen(method, path, httpx.Response(200, json=demo.PROPS))
        if m := re.fullmatch(r"/shops/\d+/listings/(\d+)/properties/(\d+)", path):
            listing, prop = int(m.group(1)), int(m.group(2))
            if method == "DELETE":
                self.properties.get(listing, {}).pop(prop, None)
                return self._seen(method, path, httpx.Response(204))
            text = request.content.decode()
            body = json.loads(text) if text.startswith("{") else {k: v for k, v in parse_qs(text).items()}
            row = {"property_id": prop, "value_ids": [int(i) for i in _many(body.get("value_ids"))],
                   "values": [str(v) for v in _many(body.get("values"))],
                   "scale_id": int(_many(body.get("scale_id"))[0]) if _many(body.get("scale_id")) else None}
            self.properties.setdefault(listing, {})[prop] = row
            return self._seen(method, path, httpx.Response(200, json=row))
        if method == "GET" and (m := re.fullmatch(r"(?:/shops/\d+)?/listings/(\d+)/properties", path)):
            rows = list(self.properties.get(int(m.group(1)), {}).values())
            return self._seen(method, path, httpx.Response(200, json={"count": len(rows), "results": rows}))
        if method == "POST" and (m := re.fullmatch(r"/shops/(\d+)/listings", path)):
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            response = await super().handle(request)
            if response.status_code == 201:
                listing = response.json()["listing_id"]
                self._forms[listing] = form
                record = {"shop_id": int(m.group(1)), "listing_id": listing, "form": form}
                self.sent.append(record)
                if self._on_sent is not None:
                    await self._on_sent(record)
            return response
        if method == "GET" and (m := re.fullmatch(r"/listings/(\d+)", path)):
            response = await super().handle(request)
            form = self._forms.get(int(m.group(1)))
            if response.status_code != 200 or form is None:
                return response
            # As Etsy answers: what the listing was created with, in getListing's own shapes.
            row = response.json()
            row.update({
                "description": form.get("description", ""), "tags": _split(form.get("tags")),
                "materials": _split(form.get("materials")), "quantity": int(form.get("quantity") or 0),
                "is_supply": str(form.get("is_supply", "")).lower() == "true",
                "shipping_profile_id": _int(form.get("shipping_profile_id")), "return_policy_id": _int(form.get("return_policy_id")),
                "readiness_state_id": _int(form.get("readiness_state_id")), "shop_section_id": _int(form.get("shop_section_id")),
                "price": {"amount": round(float(form.get("price") or 0) * 100), "divisor": 100, "currency_code": LISTS["currency"]},
            })
            return httpx.Response(200, json=row)
        return await super().handle(request)

    def _seen(self, method: str, path: str, response: httpx.Response) -> httpx.Response:
        self.requests.append(method + " " + _ID.sub("/{id}", path))
        return response


def _many(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    items = value if isinstance(value, list) else [value]
    out: list[Any] = []
    for item in items:
        out.extend(str(item).split(",") if isinstance(item, str) else [item])
    return [i for i in out if i != ""]


def _split(value: str | None) -> list[str]:
    return [v for v in (value or "").split(",") if v]


def _int(value: str | None) -> int | None:
    return int(value) if value not in (None, "") else None
