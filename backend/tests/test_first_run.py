"""The first run (pipeline/first_run.py, api/first_run.py): a path to a finished draft preview that writes nothing.

The sample text keeps the rules the app writes by; the pictures come from the real engine, the same each time; the whole
path adds no row, queues no job, calls no model and needs no shop; signing in is still required.
"""

from __future__ import annotations

import io

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import text

from app.api import deps
from app.compliance import trademarks
from app.db.models import Base
from app.pipeline import etsy_text, first_run, listing_health, mockups, search_rules
from tests.test_api import client  # noqa: F401  (fixture)

BODY = first_run.TEXT


def test_the_sample_text_keeps_the_rules_it_is_shown_as_an_example_of() -> None:
    assert etsy_text.title_errors(BODY["title"]) == [] and etsy_text.tag_errors(BODY["tags"]) == []
    assert search_rules.title_errors(BODY["title"], search_rules.SEARCH_TITLE) == []
    assert len(BODY["tags"]) == 13 and len(set(BODY["tags"])) == 13
    assert search_rules.same_search_tags(BODY["tags"]) == []
    health = listing_health.score({
        "title": BODY["title"], "tags": BODY["tags"], "description": BODY["description"], "materials": BODY["materials"],
        "images": [{}] * 5, "shop_section_id": 1,
    }, properties=[{"property_id": 1, "value_ids": [1], "values": ["x"]}])
    assert health.score == 100, [c for c in health.checks if c.verdict not in ("pass", "note")]
    assert trademarks.brand_warnings(BODY["title"], BODY["tags"], BODY["description"], trademarks.Blocklist()) == []


def test_the_area_is_a_chest_on_the_shirt_the_engine_accepts() -> None:
    assert mockups.clean_print_area(first_run.AREA) == first_run.AREA


@pytest.mark.parametrize("colour", list(first_run.COLOURS))
def test_each_mockup_is_the_design_on_that_shirt_and_the_same_each_time(colour: str) -> None:
    first_run.mockup.cache_clear()
    one = first_run.mockup(colour)
    first_run.mockup.cache_clear()
    assert first_run.mockup(colour) == one
    shirt = Image.open(io.BytesIO(first_run.blank(colour))).convert("RGB")
    printed = Image.open(io.BytesIO(one)).convert("RGB")
    assert printed.size == shirt.size
    w, h = printed.size
    middle = (int(0.5 * w), int(0.32 * h))
    corner = (5, 5)
    assert sum(abs(a - b) for a, b in zip(printed.getpixel(middle), shirt.getpixel(middle))) > 30, "the design is printed on the chest"
    assert sum(abs(a - b) for a, b in zip(printed.getpixel(corner), shirt.getpixel(corner))) <= 6, "and only there"


async def _counts(client) -> dict[str, int]:  # noqa: ANN001, F811
    async with client.sm() as s:
        out = {}
        for table in Base.metadata.sorted_tables:
            out[table.name] = (await s.execute(text(f'select count(*) from "{table.name}"'))).scalar_one()
        return out


class NoQueue:
    calls: list = []

    async def enqueue(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        self.calls.append(args)


async def test_the_whole_path_works_without_a_shop_and_writes_nothing(client: AsyncClient, monkeypatch) -> None:  # noqa: F811, ANN001
    queue = NoQueue()
    client.app.dependency_overrides[deps.get_enqueuer] = lambda: queue  # type: ignore[attr-defined]

    def no_model(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("the first run calls no model")

    monkeypatch.setattr("app.pipeline.llm.client_for", no_model)
    before = await _counts(client)
    out = await client.get("/api/first-run")
    assert out.status_code == 200, out.text
    answer = out.json()
    assert answer["title"] == BODY["title"] and answer["tags"] == BODY["tags"] and answer["area"] == first_run.AREA
    for name in first_run.IMAGES:
        image = await client.get("/api/first-run/image", params={"name": name, "v": first_run.VERSION})
        assert image.status_code == 200, name
        assert image.headers["content-type"].startswith("image/")
        assert "immutable" in image.headers["cache-control"] and image.headers["etag"]
    assert await _counts(client) == before, "no table gained or lost a row"
    assert queue.calls == []


async def test_an_old_url_is_not_kept_and_a_repeat_is_a_304(client: AsyncClient) -> None:  # noqa: F811
    first = await client.get("/api/first-run/image", params={"name": "design"})
    assert "immutable" not in first.headers["cache-control"]
    again = await client.get("/api/first-run/image", params={"name": "design"}, headers={"if-none-match": first.headers["etag"]})
    assert again.status_code == 304


async def test_only_the_samples_own_pictures_are_served(client: AsyncClient) -> None:  # noqa: F811
    for name in ("mockup-red", "../../etc/passwd", ""):
        assert (await client.get("/api/first-run/image", params={"name": name})).status_code == 404


async def test_signing_in_is_required(client: AsyncClient) -> None:  # noqa: F811
    client.cookies.clear()
    assert (await client.get("/api/first-run")).status_code == 401
    assert (await client.get("/api/first-run/image", params={"name": "design"})).status_code == 401


def test_the_seller_facing_answer_carries_no_cost_and_no_request_count() -> None:
    from app.api.first_run import SampleOut

    for word in ("cost", "token", "model", "request"):
        assert not any(word in field for field in SampleOut.model_fields), word
