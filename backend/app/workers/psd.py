"""Converting a seller's Photoshop mockup into a template, in the worker (feature/psd-templates).

**One conversion at a time, out of everyone's way.** ``convert_psd`` takes a Redis lock (``psd-convert``); a
second one waits ``BUSY_RETRY_SECONDS`` and tries again. The work itself happens in a child process
(``pipeline/psd_convert.py`` through ``psd_sandbox.run_child``) at the lowest CPU priority, with its own memory
cap (``MEMORY_MB``) and a 3-minute CPU limit, started from a thread: the worker's event loop keeps serving
drafts, publishing and listing writing while a PSD converts, and a crash in the parser fails only this file.

After a successful conversion the template's photo holds the base (``image_key``), the overlays and the mask
(``mockup_photo.psd``), and a preview with a sample design (our own, ``app/examples``). The PSD is deleted when
it has only one smart object; with several it stays until the seller chooses ("Use this template") or for
``PsdUpload.FILE_HOURS``, so a different choice can be converted again. A failed conversion keeps it as long, for
a retry. No Etsy call, no AI call.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from arq import Retry

from app.core.config import get_settings

logger = logging.getLogger(__name__)

LOCK = "psd-convert"
LOCK_SECONDS = 600
BUSY_RETRY_SECONDS = 15
#: The conversion's memory cap. The samples peaked at 265-574 MB (docs/psd-findings.md); the cap leaves room
#: for 6000 px files, and the worker container keeps room for a mockup run beside it.
MEMORY_MB = 1200
SECONDS = 180
SAMPLE_DESIGN = Path(__file__).resolve().parent.parent / "examples" / "tomato-season.png"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def incoming_key(tenant_id: uuid.UUID, upload_id: uuid.UUID) -> str:
    return f"{tenant_id}/psd/{upload_id}.psd"


def photo_prefix(tenant_id: uuid.UUID, photo_id: uuid.UUID) -> str:
    return f"{tenant_id}/templates/{photo_id}/psd"


def sample_design() -> Any:
    """Our own example design as uint8 RGBA (never a seller's)."""
    import numpy as np
    from PIL import Image

    with Image.open(SAMPLE_DESIGN) as im:
        return np.asarray(im.convert("RGBA"))


def render_preview(folder: str) -> bytes:
    """The template with our sample design placed, as JPEG (what the seller checks before using it)."""
    import io

    from PIL import Image

    from app.pipeline import psd_template

    spec, base, mask, _content, _data = psd_template.load_dir(folder)
    out = io.BytesIO()
    Image.fromarray(psd_template.render(spec, base, sample_design(), mask), "RGB").save(
        out, "JPEG", quality=90, subsampling=0)
    return out.getvalue()


def _run(path: str, folder: str, choice: int | None) -> tuple[dict[str, Any], float, int]:
    from app.pipeline import psd_sandbox

    started = time.monotonic()
    said = psd_sandbox.run_child("app.pipeline.psd_convert", [path, folder, "-" if choice is None else str(choice)],
                                 memory_mb=MEMORY_MB, seconds=SECONDS)
    with open(os.path.join(folder, "spec.json"), encoding="utf-8") as fh:
        spec = json.load(fh)
    return spec, time.monotonic() - started, int(json.loads(said or b"{}").get("peak_mb", 0))


async def convert_psd(ctx: dict[str, Any], upload_id: str) -> str:
    redis = ctx["redis"]
    if not await redis.set(LOCK, upload_id, nx=True, ex=LOCK_SECONDS):
        raise Retry(defer=BUSY_RETRY_SECONDS)  # another PSD is converting: one at a time
    try:
        return await _convert(ctx, uuid.UUID(upload_id))
    finally:
        await redis.delete(LOCK)


async def _convert(ctx: dict[str, Any], upload_id: uuid.UUID) -> str:
    from sqlalchemy import func, select

    from app.core import audit, storage_cap
    from app.core.image_work import run as image_run
    from app.db.models import MockupPhoto, MockupSubgroup, MockupTemplate, PsdUpload, Tenant
    from app.pipeline import mockups
    from app.pipeline.psd_sandbox import ParseFailed
    from app.pipeline.storage import LocalStorage
    from app.workers.mockups import measure_colour

    settings = get_settings()
    storage = ctx.get("storage") or LocalStorage(settings.storage_dir)
    async with ctx["sessionmaker"]() as session:
        upload = await session.get(PsdUpload, upload_id)
        if upload is None or upload.status not in ("queued", "converting") or not upload.file_key:
            return "nothing"
        tenant = await session.get(Tenant, upload.tenant_id)
        if tenant is None or tenant.deletion_requested_at is not None:
            return "nothing"
        upload.status, upload.error, upload.updated_at = "converting", None, _now()
        await session.commit()
        path = str(storage._path(upload.file_key))  # noqa: SLF001 - the local file, read by the child
        folder = tempfile.mkdtemp(prefix="psd-")
        try:
            try:
                spec, took, peak = await asyncio.to_thread(_run, path, folder, upload.choice)
            except ParseFailed as exc:
                upload.status, upload.error = "failed", _plain(str(exc))
                upload.finished_at = upload.updated_at = _now()
                await session.commit()
                logger.info("psd conversion %s failed: %s", upload.id, exc)
                return "failed"
            preview = await image_run(render_preview, folder)

            photo = await session.get(MockupPhoto, upload.photo_id) if upload.photo_id else None
            template = await session.get(MockupTemplate, upload.template_id) if upload.template_id else None
            stem = PurePosixPath(upload.filename.replace("\\", "/")).stem[:120] or "Photoshop mockup"
            if template is None:
                last = await session.scalar(select(func.max(MockupTemplate.position)).where(MockupTemplate.tenant_id == tenant.id))
                template = MockupTemplate(tenant_id=tenant.id, name=stem, position=int(last or 0) + 1)
                session.add(template)
                await session.flush()
                group = MockupSubgroup(tenant_id=tenant.id, template_id=template.id, name=template.name, use_on="any")
                session.add(group)
                await session.flush()
                upload.template_id = template.id
            new_photo = photo is None
            if new_photo:
                group_id = await session.scalar(select(MockupSubgroup.id).where(MockupSubgroup.template_id == template.id)
                                                .order_by(MockupSubgroup.position).limit(1))
                photo = MockupPhoto(id=uuid.uuid4(), tenant_id=tenant.id, template_id=template.id, subgroup_id=group_id,
                                    name=stem, position=1, status="ready", print_area_source="psd",
                                    size=mockups.DEFAULT_SIZE, fold_strength=0.0, occlusion=False,
                                    rights_confirmed_at=upload.rights_confirmed_at)
                session.add(photo)
                await session.flush()
            old = (photo.psd or {}).get("keys", []) + ([photo.image_key] if photo.image_key else [])

            # Store what the template needs; a new name each time (the old files go after the commit).
            prefix = f"{photo_prefix(tenant.id, photo.id)}/{uuid.uuid4().hex[:8]}"
            keys: list[str] = []

            def put(name: str, data: bytes, content_type: str) -> str:
                key = f"{prefix}/{name}"
                storage.put(key, data, content_type)
                storage_cap.added(tenant.id, len(data))
                keys.append(key)
                return key

            base_key = put("base.jpg", Path(folder, "base.jpg").read_bytes(), "image/jpeg")
            overlays = [{**{k: v for k, v in o.items() if k != "file"},
                         "key": put(o["file"], Path(folder, o["file"]).read_bytes(), "image/png")}
                        for o in spec["overlays"]]
            placement = dict(spec["placement"])
            if placement.get("mask"):
                placement["mask"] = put("mask.png", Path(folder, "mask.png").read_bytes(), "image/png")
            preview_key = put("preview.jpg", preview, "image/jpeg")
            W, H = spec["width"], spec["height"]
            quad = [[x / W, y / H] for x, y in placement["quad"]]
            photo.image_key, photo.width, photo.height = base_key, W, H
            photo.mask_key = None
            photo.print_area = quad
            photo.garments = [{"print_area": quad, "detected_area": quad, "source": "psd", "confidence": 1.0}]
            photo.status, photo.error = "ready", None
            photo.colour = photo.colour_lightness = photo.colour_source = None
            photo.psd = {
                "width": W, "height": H, "full": spec["full"], "placement": placement, "overlays": overlays,
                "options": spec["options"], "choice": spec["choice"], "notes": spec["notes"],
                "preview": preview_key, "keys": keys, "upload_id": str(upload.id),
            }
            photo.version = (photo.version or 1) + 1
            await image_run(measure_colour, photo, Path(folder, "base.jpg").read_bytes())
            usable = [o for o in spec["options"] if o.get("usable")]
            upload.photo_id, upload.choice = photo.id, spec["choice"]
            upload.status, upload.took_seconds, upload.peak_mb = "ready", round(took, 2), peak
            upload.finished_at = upload.updated_at = _now()
            gone_file = None
            if len(usable) <= 1:  # nothing left to choose: the PSD is not needed any more
                gone_file, upload.file_key = upload.file_key, None
            audit.record(session, "mockup_photo.created_from_psd", actor=tenant, target_tenant_id=tenant.id, by="seller",
                         object_id=str(photo.id), rights_confirmed=True)
            await session.commit()
            for key in old:
                if key and key not in keys:
                    await asyncio.to_thread(storage.delete_with_derivatives, key)
            if gone_file:
                await asyncio.to_thread(_delete_file, storage, gone_file)
            logger.info("psd conversion %s ready in %.1fs, peak %d MB", upload.id, took, peak)
            return "ready"
        finally:
            shutil.rmtree(folder, ignore_errors=True)


def _plain(reason: str) -> str:
    """The child's reason as a sentence for the seller."""
    reason = reason.strip()
    if reason.startswith(("it ", "reading it")):
        return f"This file could not be used: {reason}."
    return reason


def _delete_file(storage: Any, key: str) -> None:
    try:
        storage._path(key).unlink(missing_ok=True)  # noqa: SLF001 - the incoming PSD, a plain file
    except (OSError, FileNotFoundError):
        pass


async def expire_psd_files(session: Any, storage: Any, now: datetime | None = None) -> int:
    """The 15-minute sweep: PSDs kept past ``FILE_HOURS`` are deleted (a failed or unchosen one, or an upload
    left half way), and finished upload rows go after ``RETENTION_DAYS``."""
    from datetime import timedelta

    from sqlalchemy import delete, select

    from app.db.models import PsdUpload

    now = now or _now()
    stale = list((await session.execute(select(PsdUpload).where(
        PsdUpload.file_key.is_not(None), PsdUpload.created_at < now - timedelta(hours=PsdUpload.FILE_HOURS),
        PsdUpload.status.not_in(("queued", "converting"))))).scalars())
    for upload in stale:
        key, upload.file_key = upload.file_key, None
        if upload.status == "receiving":
            upload.status, upload.error = "cancelled", "The upload was not finished within a day."
            upload.finished_at = now
        await session.commit()
        await asyncio.to_thread(_delete_file, storage, key)
    await session.execute(delete(PsdUpload).where(
        PsdUpload.finished_at < now - timedelta(days=PsdUpload.RETENTION_DAYS), PsdUpload.file_key.is_(None)))
    await session.commit()
    return len(stale)


class Loaded:
    """A PSD photo's template, read once per mockup run: the spec, the base, the mask."""

    def __init__(self, spec: Any, base: Any, mask: Any) -> None:
        self.spec, self.base, self.mask = spec, base, mask
        self.width, self.height = spec.width, spec.height


def load_template(storage: Any, photo: Any) -> Loaded:
    import io

    import numpy as np
    from PIL import Image

    from app.pipeline import psd_template

    data = photo.psd or {}
    overlays = [np.asarray(Image.open(io.BytesIO(storage.get(o["key"]))).convert("RGBA")) for o in data["overlays"]]
    base = np.asarray(Image.open(io.BytesIO(storage.get(photo.image_key))).convert("RGB"))
    mask_key = data["placement"].get("mask")
    mask = np.asarray(Image.open(io.BytesIO(storage.get(mask_key))).convert("L")) if mask_key else None
    return Loaded(psd_template.spec_from(data, overlays), base, mask)


def render_design(loaded: Loaded, design: Any, quality: int = 92) -> bytes:
    """One design (a pyvips image, sRGB + alpha, as the mockup run loads it) on a PSD template, as JPEG."""
    import io

    import numpy as np
    from PIL import Image

    from app.pipeline import psd_template

    image = design
    if image.bands == 3:
        image = image.bandjoin(255)
    image = image.cast("uchar")
    rgba = np.ndarray(buffer=image.write_to_memory(), dtype=np.uint8, shape=[image.height, image.width, image.bands])
    out = io.BytesIO()
    Image.fromarray(psd_template.render(loaded.spec, loaded.base, rgba[..., :4], loaded.mask), "RGB").save(
        out, "JPEG", quality=quality, subsampling=0)
    return out.getvalue()
