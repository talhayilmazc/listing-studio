"""A listing's video (feature/listing-video): the rules, the upload in pieces, the worker's reading, and the
draft that gets it, against the backend tests' fake Etsy over HTTP (tests/browser_etsy.py).

A listing without a video is made with exactly the requests it was made with before; a video Etsy refuses
leaves the draft made; "Try again" sends only the video, after looking for it; "Replace video" takes the
listing's video off first and only on the seller's confirmed click.
"""

from __future__ import annotations

import io
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.models import Asset, GeneratedContent, ListingPublication, ListingVideo
from app.pipeline import listing_videos, video_probe, video_rules
from app.pipeline.storage import LocalStorage
from app.workers import listing_video as video_worker
from app.workers import plans
from app.workers import publish as publish_worker
from tests.browser_etsy import BrowserEtsy
from tests.test_end_to_end import _Httpx, _run_drafts, _send_drafts, _world
from tests.video_builder import mov, mp4

CASES = json.loads((Path(__file__).parent / "fixtures" / "video_rule_cases.json").read_text(encoding="utf-8"))


# --- The rules, in one place ------------------------------------------------------------------------------


def test_the_rules_say_what_is_wrong_in_plain_words() -> None:
    for case in CASES["files"]:
        assert video_rules.file_problem(case["name"], case["size"]) == case["problem"], case
    for case in CASES["durations"]:
        assert video_rules.duration_problem(case["seconds"]) == case["problem"], case
        assert video_rules.duration_label(case["seconds"]) == case["label"], case
    assert video_rules.duration_problem(22) == "This video is 22 s; Etsy takes 5–15 s."  # the brief's own sentence
    # What Etsy's developer pages say, and what they do not: the limits a person has not checked are marked.
    assert (video_rules.OPERATION, video_rules.SCOPE, video_rules.UPLOADS_PER_LISTING_PER_DAY) == ("uploadListingVideo", "listings_w", 10)
    assert video_rules.VIDEOS_PER_LISTING == 1 and video_rules.ETSY_ACTIVE_VIDEOS == 2
    assert {k for k, v in video_rules.CONFIRMED.items() if v == "api"} == {"endpoint", "scope", "uploads_per_listing_per_day", "active_videos"} and video_rules.CONFIRMED["bytes"] == "unconfirmed"
    # No new scope: the app already asks for the one the upload needs.
    from app.etsy.oauth import SCOPE_REASONS

    assert video_rules.SCOPE in SCOPE_REASONS


def test_a_video_file_says_how_long_it_is_from_its_own_boxes() -> None:
    for data, seconds, audio in ((mp4(8.0), 8.0, False), (mp4(22.0, audio=True), 22.0, True), (mp4(12.5, moov_last=True, pad=70_000), 12.5, False),
                                 (mp4(9.0, version=1), 9.0, False), (mov(6.0, codec=b"hvc1"), 6.0, False)):
        probe = video_probe.read(io.BytesIO(data), len(data))
        assert (round(probe.seconds, 2), probe.width, probe.height, probe.has_audio) == (seconds, 1080, 1920, audio)
    assert video_probe.read(io.BytesIO(mov(6.0, codec=b"hvc1")), len(mov(6.0, codec=b"hvc1"))).codec == "hvc1"
    for junk in (b"", b"not a video at all, only words" * 10, b"\xff\xd8\xff\xe0" + b"\x00" * 200, mp4(8.0)[:60]):
        with pytest.raises(video_probe.NotAVideo):
            video_probe.read(io.BytesIO(junk), len(junk))


def test_a_video_uploaded_with_the_photos_joins_its_listing_by_the_same_rule() -> None:
    assert listing_videos.group_for("BR100", "anything.mp4", "folder") == "BR100"  # the same folder
    assert listing_videos.group_for(None, "BR100-video.mp4", "sku") == listing_videos.group_for(None, "BR100-front.png", "sku") == "BR100"
    assert listing_videos.group_for(None, "BR100_2.mov", "sku") == "BR100"
    assert listing_videos.group_for(None, "holiday.mp4", "sku") == "~unsorted"  # no SKU in its name: no listing is guessed
    assert listing_videos.group_for("Shots", "clip.mp4", "one") == ""


# --- The upload, in pieces, and the worker's reading ------------------------------------------------------


async def _etsy(tmp_path, test_settings: Settings, monkeypatch: pytest.MonkeyPatch, *, shops: int = 1, listings: int = 2) -> dict:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=shops, listings=listings)
    w["etsy"] = BrowserEtsy()
    monkeypatch.setattr(publish_worker, "httpx", _Httpx(w["etsy"]))
    # One storage for the API and the worker of this world (the app keeps its own between tests otherwise).
    from app.api import deps

    w["ctx"]["storage"] = LocalStorage(tmp_path / "storage")
    w["client"]._transport.app.dependency_overrides[deps.get_storage] = lambda: w["ctx"]["storage"]  # noqa: SLF001
    async with w["sm"]() as s:
        rows = (await s.execute(select(GeneratedContent.id, Asset.group_key).join(Asset, Asset.id == GeneratedContent.asset_id)
                                .where(GeneratedContent.id.in_(w["contents"])))).all()
    w["groups"] = {cid: key for cid, key in rows}
    return w


async def _add(w: dict, content: uuid.UUID, data: bytes, *, name: str = "clip.mp4", shop: uuid.UUID | None = None,
               piece: int = 200, process: bool = True) -> dict:
    a = w["client"]
    opened = await a.post(f"/api/batches/{w['batch']}/videos", json={
        "filename": name, "size": len(data), "group_key": w["groups"][content], "connection_id": str(shop) if shop else None})
    assert opened.status_code == 201, opened.text
    video = opened.json()
    for offset in range(0, len(data), piece):
        sent = await a.put(f"/api/listing-videos/{video['id']}/chunk?offset={offset}", content=data[offset:offset + piece],
                           headers={"Content-Type": "application/octet-stream"})
        assert sent.status_code == 200, sent.text
        video = sent.json()
    assert video["status"] == "processing" and video["received"] == len(data)
    assert ("process_listing_video", (video["id"],)) in w["queue"].calls
    if process:
        outcome = await video_worker.process_listing_video(w["ctx"], video["id"])
        video = (await a.get(f"/api/listing-videos/{video['id']}")).json()
        assert (outcome, video["status"]) in (("ready", "ready"), ("refused", "failed")), (outcome, video)
    return video


async def test_a_video_arrives_in_pieces_and_is_read_by_the_worker(tmp_path, test_settings, monkeypatch) -> None:
    w = await _etsy(tmp_path, test_settings, monkeypatch)
    a, first = w["client"], w["contents"][0]
    # Nothing yet: the batch has no video, and says so with an empty list.
    assert (await a.get(f"/api/batches/{w['batch']}/videos")).json()["videos"] == []

    # Refused before a byte is sent: not a video, and larger than Etsy takes.
    wrong = await a.post(f"/api/batches/{w['batch']}/videos", json={"filename": "clip.avi", "size": 1000, "group_key": w["groups"][first]})
    assert wrong.status_code == 422 and wrong.json()["detail"] == "Choose an MP4 or MOV video."
    big = await a.post(f"/api/batches/{w['batch']}/videos", json={"filename": "clip.mp4", "size": 130 * 1024 * 1024, "group_key": w["groups"][first]})
    assert big.status_code == 413 and big.json()["detail"] == "This video is 130 MB; Etsy takes videos up to 100 MB."

    data = mp4(8.0, audio=True)
    opened = (await a.post(f"/api/batches/{w['batch']}/videos", json={"filename": "C:\\shots\\clip.mp4", "size": len(data),
                                                                    "group_key": w["groups"][first]})).json()
    assert opened["filename"] == "clip.mp4" and opened["chunk_size"] == 8 * 1024 * 1024 and opened["status"] == "receiving"
    url = f"/api/listing-videos/{opened['id']}/chunk"
    put = lambda offset, body: a.put(f"{url}?offset={offset}", content=body, headers={"Content-Type": "application/octet-stream"})  # noqa: E731
    assert (await put(0, data[:300])).json()["received"] == 300
    assert (await put(0, data[:300])).json()["received"] == 300  # the same piece again changes nothing
    gap = await put(500, data[500:600])
    assert gap.status_code == 409 and gap.json()["detail"]["received"] == 300  # told where the file ends
    assert (await put(len(data) - 10, data[-10:] + b"x" * 20)).status_code in (409, 422)
    done = (await put(300, data[300:])).json()
    assert done["status"] == "processing" and done["received"] == len(data)
    assert (await put(300, data[300:])).json()["status"] == "processing"  # the last piece twice: still one video

    assert await video_worker.process_listing_video(w["ctx"], opened["id"]) == "ready"
    ready = (await a.get(f"/api/batches/{w['batch']}/videos")).json()
    assert ready["limits"]["max_seconds"] == 15 and len(ready["videos"]) == 1
    video = ready["videos"][0]
    assert (video["status"], video["seconds"], video["duration"], video["width"], video["height"], video["error"]) == \
        ("ready", 8.0, "0:08", 1080, 1920, None)
    # Stored whole, under the batch's own folder (so it goes with the batch).
    async with w["sm"]() as s:
        row = await s.get(ListingVideo, uuid.UUID(opened["id"]))
        assert row.file_key.startswith(f"{w['tenant']}/{w['batch']}/video/") and row.has_audio is True
        assert w["ctx"]["storage"].get(row.file_key) == data

    # A video Etsy would not take for its length: the brief's own sentence, and the file is not kept.
    long = await _add(w, w["contents"][1], mp4(22.0))
    assert long["status"] == "failed" and long["error"] == "This video is 22 s; Etsy takes 5–15 s."
    junk = await _add(w, w["contents"][1], b"\x00\x00\x00\x08free" + b"words " * 100)
    assert junk["status"] == "failed" and junk["error"] == video_rules.UNREADABLE
    async with w["sm"]() as s:
        assert (await s.get(ListingVideo, uuid.UUID(long["id"]))).file_key is None

    # Replace: the new one takes the old one's place once it is ready; a refused one changes nothing.
    again = await _add(w, first, mov(6.0), name="second.mov")
    listed = (await a.get(f"/api/batches/{w['batch']}/videos")).json()["videos"]
    assert [v["filename"] for v in listed if v["group_key"] == w["groups"][first]] == ["second.mov"] and again["duration"] == "0:06"
    refused = await _add(w, first, mp4(40.0), name="third.mp4")
    assert refused["status"] == "failed"
    listed = (await a.get(f"/api/batches/{w['batch']}/videos")).json()["videos"]
    assert sorted(v["status"] for v in listed if v["group_key"] == w["groups"][first]) == ["failed", "ready"]

    # Remove: ours only.
    assert (await a.delete(f"/api/listing-videos/{again['id']}")).status_code == 204
    assert (await a.get(f"/api/listing-videos/{again['id']}")).status_code == 404
    assert w["etsy"].requests == []  # nothing here ever asked Etsy for anything


# --- The draft ----------------------------------------------------------------------------------------------


async def test_a_draft_gets_its_video_and_a_listing_without_one_is_made_as_before(tmp_path, test_settings, monkeypatch) -> None:
    # Two worlds, the same but for one video on the first listing.
    plain = await _etsy(tmp_path / "plain", test_settings, monkeypatch)
    await _run_drafts(plain, await _send_drafts(plain))
    before = list(plain["etsy"].requests)
    plain_forms = [r["form"] for r in plain["etsy"].sent]

    w = await _etsy(tmp_path / "video", test_settings, monkeypatch)
    data = mp4(8.0)
    await _add(w, w["contents"][0], data)
    await _run_drafts(w, await _send_drafts(w))
    etsy = w["etsy"]
    # The video went to the first listing's draft, once, after its images, as a multipart upload of the file.
    assert len(etsy.video_uploads) == 1
    upload = etsy.video_uploads[0]
    async with w["sm"]() as s:
        pubs = {p.content_id: p for p in (await s.execute(select(ListingPublication))).scalars()}
    with_video, without = pubs[w["contents"][0]], pubs[w["contents"][1]]
    assert upload["listing_id"] == with_video.etsy_listing_id and upload["name"] == "clip.mp4" and upload["bytes"] > len(data)
    assert upload["content_type"] == "multipart/form-data"
    assert (with_video.video_state, with_video.etsy_video_id, with_video.video_error) == ("attached", upload["video_id"], None)
    assert (without.video_state, without.etsy_video_id, without.video_sent_id) == (None, None, None)
    # Everything else is what it was: the same requests in the same order, plus the one upload at the end of
    # the first draft; the same createDraftListing for both listings.
    video_request = "POST /shops/{id}/listings/{id}/videos"
    assert [r for r in etsy.requests if r != video_request] == before
    assert etsy.requests.count(video_request) == 1
    first_images = max(i for i, r in enumerate(etsy.requests[:etsy.requests.index(video_request)]) if r.endswith("/images"))
    assert first_images < etsy.requests.index(video_request)
    assert [r["form"] for r in etsy.sent] == plain_forms
    # The page is told what became of it.
    drafts = (await w["client"].get(f"/api/batches/{w['batch']}/videos")).json()["drafts"]
    assert sorted((d["video_state"] or "none") for d in drafts) == ["attached", "none"]


async def test_a_video_etsy_refuses_leaves_the_draft_made_and_try_again_sends_only_the_video(tmp_path, test_settings, monkeypatch) -> None:
    w = await _etsy(tmp_path, test_settings, monkeypatch, listings=1)
    a, etsy, content = w["client"], w["etsy"], w["contents"][0]
    video = await _add(w, content, mp4(8.0))
    etsy.refuse_video = "The video must be between 5 and 15 seconds long."
    await _run_drafts(w, await _send_drafts(w))  # the job succeeds: the draft is made
    async with w["sm"]() as s:
        publication = (await s.execute(select(ListingPublication))).scalars().one()
        assert publication.state == "draft" and publication.video_state == "failed"
        assert "The video must be between 5 and 15 seconds long." in publication.video_error
    draft = (await a.get(f"/api/batches/{w['batch']}/videos")).json()["drafts"][0]
    assert draft["video_state"] == "failed" and "between 5 and 15 seconds" in draft["video_error"]

    # "Try again": confirmed, queued, and only the video is sent (after looking on the draft for it).
    body = {"content_id": str(content), "connection_id": str(w["shops"][0])}
    assert (await a.post(f"/api/listing-videos/{video['id']}/send", json=body)).status_code == 422  # no confirmation, nothing queued
    etsy.refuse_video = None
    sent = await a.post(f"/api/listing-videos/{video['id']}/send", json={**body, "confirm": True})
    assert sent.status_code == 200 and sent.json()["video_state"] == "sending"
    assert (await a.post(f"/api/listing-videos/{video['id']}/send", json={**body, "confirm": True})).status_code == 409  # already on its way
    job = [c for c in w["queue"].calls if c[0] == "attach_listing_video"]
    assert len(job) == 1 and job[0][1][0] == f"{publication.id}|{video['id']}|retry"
    mark = len(etsy.requests)
    assert await video_worker.attach_listing_video(w["ctx"], job[0][1][0]) == "attached"
    assert etsy.requests[mark:] == ["GET /listings/{id}/videos", "POST /shops/{id}/listings/{id}/videos"]
    assert len(etsy.sent) == 1  # never a second listing
    async with w["sm"]() as s:
        publication = (await s.execute(select(ListingPublication))).scalars().one()
        assert (publication.video_state, publication.video_error) == ("attached", None) and publication.etsy_video_id

    # Sent again by mistake (an answer that never came): the video is found on the draft and not sent twice.
    mark = len(etsy.requests)
    assert await video_worker.attach_listing_video(w["ctx"], job[0][1][0]) == "attached"
    assert etsy.requests[mark:] == ["GET /listings/{id}/videos"] and len(etsy.video_uploads) == 1


async def test_replace_video_takes_the_old_one_off_only_on_the_sellers_click(tmp_path, test_settings, monkeypatch) -> None:
    w = await _etsy(tmp_path, test_settings, monkeypatch, listings=1)
    a, etsy, content = w["client"], w["etsy"], w["contents"][0]
    await _add(w, content, mp4(8.0), name="first.mp4")
    await _run_drafts(w, await _send_drafts(w))
    listing = etsy.sent[0]["listing_id"]
    old = etsy.videos[listing][0]["video_id"]
    # A new video on the card: the draft on Etsy is not touched by that.
    new = await _add(w, content, mp4(10.0), name="second.mp4")
    assert [v["video_id"] for v in etsy.videos[listing]] == [old] and len(etsy.video_uploads) == 1
    sent = await a.post(f"/api/listing-videos/{new['id']}/send", json={
        "content_id": str(content), "connection_id": str(w["shops"][0]), "replace": True, "confirm": True})
    assert sent.status_code == 200
    arg = [c for c in w["queue"].calls if c[0] == "attach_listing_video"][-1][1][0]
    assert arg.endswith("|replace")
    mark = len(etsy.requests)
    assert await video_worker.attach_listing_video(w["ctx"], arg) == "attached"
    assert etsy.requests[mark:] == ["GET /listings/{id}/videos", "DELETE /shops/{id}/listings/{id}/videos/{id}",
                                    "POST /shops/{id}/listings/{id}/videos"]
    assert [v["video_id"] for v in etsy.videos[listing]] != [old] and len(etsy.videos[listing]) == 1
    assert etsy.video_uploads[-1]["name"] == "second.mp4"
    async with w["sm"]() as s:
        publication = (await s.execute(select(ListingPublication))).scalars().one()
        assert publication.video_state == "attached" and str(publication.video_sent_id) == new["id"]


async def test_a_scheduled_draft_gets_the_video_and_a_shop_can_have_its_own(tmp_path, test_settings, monkeypatch) -> None:
    w = await _etsy(tmp_path, test_settings, monkeypatch, shops=2, listings=1)
    a, etsy, content = w["client"], w["etsy"], w["contents"][0]
    await _add(w, content, mp4(8.0), name="everywhere.mp4")
    await _add(w, content, mp4(9.0), name="second-shop.mp4", shop=w["shops"][1])
    listed = (await a.get(f"/api/batches/{w['batch']}/videos")).json()["videos"]
    assert sorted((v["filename"], v["connection_id"] is not None) for v in listed) == [("everywhere.mp4", False), ("second-shop.mp4", True)]
    # Sent to both shops on a schedule: each planned draft is made when its time comes, with its video.
    await a.put(f"/api/batches/{w['batch']}/send-to", json={"send_to": {"group_ids": [], "connection_ids": [str(s) for s in w["shops"]]}})
    schedule = {"start_date": (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat(), "per_shop_per_day": 2,
                "window_start": "09:00", "window_end": "17:00", "spacing_minutes": 30, "stagger_minutes": 5}
    spread = await a.post(f"/api/batches/{w['batch']}/send", json={"when": "spread", "schedule": schedule})
    assert spread.status_code == 200 and spread.json()["scheduled"] == 2, spread.text
    assert etsy.video_uploads == []  # nothing goes before its time
    async with w["sm"]() as s:
        released = await plans.release_due(s, w["ctx"]["enqueue"], now=datetime.now(timezone.utc) + timedelta(days=4))
    assert released == 2
    for args in [e for e in w["enqueued"] if e[0] == "run_publish_job"]:
        assert await publish_worker.run_publish_job(w["ctx"], args[1]) == "succeeded"
    by_shop = {u["shop_id"]: u["name"] for u in etsy.video_uploads}
    assert by_shop == {900: "everywhere.mp4", 901: "second-shop.mp4"}  # the second shop's own, the listing's elsewhere


async def test_a_groups_video_goes_with_its_photos(tmp_path, test_settings, monkeypatch) -> None:
    w = await _etsy(tmp_path, test_settings, monkeypatch, listings=1)
    video = await _add(w, w["contents"][0], mp4(8.0))
    storage = w["ctx"]["storage"]
    now = datetime.now(timezone.utc)
    async with w["sm"]() as s:
        row = await s.get(ListingVideo, uuid.UUID(video["id"]))
        key = row.file_key
        assert await listing_videos.remove_files(s, storage, row.batch_id, row.group_key, now, apply=False) == (1, row.size)
        assert storage.exists(key)  # a count only
        assert (await listing_videos.remove_files(s, storage, row.batch_id, row.group_key, now, apply=True))[0] == 1
        await s.commit()
    assert not storage.exists(key)
    gone = (await w["client"].get(f"/api/listing-videos/{video['id']}")).json()
    assert gone["kept"] is False and gone["error"] == listing_videos.NO_LONGER_KEPT
    # A draft made now is made without it, and says nothing went wrong.
    await _run_drafts(w, await _send_drafts(w))
    assert w["etsy"].video_uploads == []
    refused = await w["client"].post(f"/api/listing-videos/{video['id']}/send", json={
        "content_id": str(w["contents"][0]), "connection_id": str(w["shops"][0]), "confirm": True})
    assert refused.status_code == 409 and refused.json()["detail"] == listing_videos.NO_LONGER_KEPT


def test_a_poster_frame_is_made_from_a_real_video(tmp_path) -> None:
    exe = listing_videos.ffmpeg()
    if exe is None:
        pytest.skip("no frame reader in this image")
    import subprocess

    clip = tmp_path / "clip.mp4"
    subprocess.run([exe, "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=6:size=320x240:rate=5",
                    "-pix_fmt", "yuv420p", str(clip)], check=True, timeout=60)
    with open(clip, "rb") as fh:
        probe = video_probe.read(fh, clip.stat().st_size)
    assert (round(probe.seconds), probe.width, probe.height, probe.has_audio) == (6, 320, 240, False)
    data = listing_videos.poster(clip, probe.seconds)
    assert data is not None and data[:2] == b"\xff\xd8" and len(data) > 500
    # A file with nothing to show has no poster, and that is not an error.
    blank = tmp_path / "blank.mp4"
    blank.write_bytes(mp4(8.0))
    assert listing_videos.poster(blank, 8.0) is None
