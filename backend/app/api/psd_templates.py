"""Uploading a Photoshop mockup and turning it into a template (feature/psd-templates, step 3).

* ``POST /api/psd-uploads`` opens an upload: file name, size, the seller's rights confirmation. Refused at once
  above ``MAX_BYTES`` (500 MB), for a file that is not .psd/.psb, a full account or a full disk.
* ``PUT /api/psd-uploads/{id}/chunk?offset=N`` sends one piece, at most ``CHUNK_BYTES`` (8 MB: far under
  Cloudflare's 100 MB per request on every plan, and under the 10 MB a Next.js server buffers of a request it
  proxies; in production ``/api`` goes straight to the API, locally and in the browser tests through Next, where a
  16 MB piece was cut off). A piece is first written to its own file (no database
  connection is held while it arrives), then appended under the row lock if it starts where the file ends. A
  piece that was already received (a retry after a lost answer) is accepted and changes nothing; one that would
  leave a gap is refused with where to start (409). After a network drop the page asks how much arrived
  (``GET``) and goes on from there.
* ``POST …/complete`` queues the conversion (``workers/psd.py``: one at a time, in a child process).
* ``POST …/choice`` converts again with another smart object (while the PSD is kept).
* ``GET …/preview`` the template with our sample design; ``POST …/use`` "Use this template";
  ``DELETE`` cancels (the template made from it goes too, unless it was used).

Everything is the account's own: another account's upload is 404. No Etsy call, no AI call.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import images
from app.api.deps import Enqueuer, active_tenant, get_enqueuer, get_session, get_storage
from app.core import audit, storage_cap
from app.db.models import MockupPhoto, MockupTemplate, PsdUpload, Tenant
from app.pipeline.storage import Storage
from app.workers import psd as psd_work

router = APIRouter(prefix="/api", tags=["psd-templates"])

MAX_BYTES = 500 * 1024 * 1024
CHUNK_BYTES = 8 * 1024 * 1024
#: Kept free on the server's disk beyond the upload itself (the disk is 30 GB and shared).
DISK_RESERVE = 3 * 1024 ** 3
RIGHTS_STATEMENT = "I have the right to use this mockup for my listings."
EXTENSIONS = (".psd", ".psb")
TOO_BIG = ("This file is larger than 500 MB. In Photoshop, delete the layers the mockup does not need or save a "
           "smaller copy (Image > Image Size), then upload it again.")
NOT_PSD = "Choose a Photoshop file (.psd or .psb)."
DISK_FULL = "The server is short of space right now. Please try again later."
NOT_KEPT = ("The Photoshop file is no longer kept (we keep it for a day). Upload it again to choose another "
            "smart object.")


class UploadIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)
    rights_confirmed: bool = False


class ChoiceIn(BaseModel):
    index: int = Field(ge=0, le=10_000)


class OptionOut(BaseModel):
    index: int
    name: str
    #: Where it sits on the stored image (left, top, right, bottom), for the picture.
    box: list[int] | None = None
    usable: bool = True


class UploadOut(BaseModel):
    id: uuid.UUID
    filename: str
    size: int
    received: int
    chunk_size: int = CHUNK_BYTES
    #: receiving · queued · converting · ready · used · failed · cancelled
    status: str
    error: str | None = None
    choice: int | None = None
    options: list[OptionOut] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    #: The PSD is still kept, so another smart object can be chosen.
    can_choose: bool = False
    template_id: uuid.UUID | None = None
    photo_id: uuid.UUID | None = None
    preview_url: str | None = None
    width: int | None = None
    height: int | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _own(session: AsyncSession, tenant: Tenant, upload_id: uuid.UUID, *, lock: bool = False) -> PsdUpload:
    q = select(PsdUpload).where(PsdUpload.id == upload_id, PsdUpload.tenant_id == tenant.id)
    if lock:
        q = q.with_for_update()
    upload = (await session.execute(q)).scalar_one_or_none()
    if upload is None:
        raise HTTPException(status_code=404, detail="upload not found")
    return upload


async def _out(session: AsyncSession, upload: PsdUpload) -> UploadOut:
    photo = await session.get(MockupPhoto, upload.photo_id) if upload.photo_id else None
    data = (photo.psd or {}) if photo is not None else {}
    return UploadOut(
        id=upload.id, filename=upload.filename, size=upload.size, received=upload.received, status=upload.status,
        error=upload.error, choice=upload.choice,
        options=[OptionOut(index=o["index"], name=o["name"], box=o.get("box"), usable=bool(o.get("usable", True)))
                 for o in data.get("options", [])],
        notes=list(data.get("notes", [])), can_choose=upload.file_key is not None and upload.status == "ready",
        template_id=upload.template_id, photo_id=upload.photo_id,
        preview_url=(f"/api/psd-uploads/{upload.id}/preview?v={photo.version}"
                     if photo is not None and data.get("preview") and upload.status in ("ready", "used") else None),
        width=data.get("width"), height=data.get("height"),
    )


def _path(storage: Storage, key: str) -> Any:
    path = storage._path(key)  # noqa: SLF001 - the incoming PSD is a plain local file, appended piece by piece
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@router.post("/psd-uploads", response_model=UploadOut, status_code=201)
async def open_upload(
    body: UploadIn,
    session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> UploadOut:
    """A new upload; nothing is stored until the first piece arrives."""
    from app.api.mockups import MAX_TEMPLATES_PER_ACCOUNT, _room

    if not body.rights_confirmed:
        raise HTTPException(status_code=422, detail=f"Please confirm: “{RIGHTS_STATEMENT}”")
    if not body.filename.lower().endswith(EXTENSIONS):
        raise HTTPException(status_code=422, detail=NOT_PSD)
    if body.size > MAX_BYTES:
        raise HTTPException(status_code=413, detail=TOO_BIG)
    count = await session.scalar(select(func.count()).select_from(MockupTemplate).where(MockupTemplate.tenant_id == tenant.id))
    if int(count or 0) >= MAX_TEMPLATES_PER_ACCOUNT:
        raise HTTPException(status_code=409, detail=f"An account keeps up to {MAX_TEMPLATES_PER_ACCOUNT} templates; delete one first.")
    await _room(session, storage, tenant, body.size)
    root = storage._path(f"{tenant.id}/psd/x").parent  # noqa: SLF001
    root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root).free < body.size + DISK_RESERVE:
        raise HTTPException(status_code=507, detail=DISK_FULL)
    upload = PsdUpload(id=uuid.uuid4(), tenant_id=tenant.id, filename=PurePosixPath(body.filename.replace("\\", "/")).name[:255],
                       size=body.size, rights_confirmed_at=_now())
    upload.file_key = psd_work.incoming_key(tenant.id, upload.id)
    session.add(upload)
    audit.record(session, "psd_upload.started", actor=tenant, target_tenant_id=tenant.id, by="seller",
                 object_id=str(upload.id), rights_confirmed=True)
    await session.commit()
    return await _out(session, upload)


@router.get("/psd-uploads", response_model=list[UploadOut])
async def open_uploads(
    session: AsyncSession = Depends(get_session, scope="function"), tenant: Tenant = Depends(active_tenant)
) -> list[UploadOut]:
    """The account's uploads still waiting for something (to finish, to convert, to be used): a page that was
    closed half way picks them up again."""
    rows = (await session.execute(select(PsdUpload).where(
        PsdUpload.tenant_id == tenant.id, PsdUpload.status.in_(("receiving", "queued", "converting", "ready", "failed")))
        .order_by(PsdUpload.created_at.desc()).limit(20))).scalars()
    return [await _out(session, u) for u in rows]


@router.get("/psd-uploads/{upload_id}", response_model=UploadOut)
async def upload_state(
    upload_id: uuid.UUID, session: AsyncSession = Depends(get_session, scope="function"), tenant: Tenant = Depends(active_tenant)
) -> UploadOut:
    return await _out(session, await _own(session, tenant, upload_id))


@router.put("/psd-uploads/{upload_id}/chunk", response_model=UploadOut)
async def put_chunk(
    upload_id: uuid.UUID,
    request: Request,
    offset: int = Query(ge=0),
    session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> UploadOut:
    upload = await _own(session, tenant, upload_id)
    if upload.status != "receiving" or not upload.file_key:
        raise HTTPException(status_code=409, detail="This upload is not receiving any more.")
    key = upload.file_key
    await session.commit()  # no connection is held while the piece arrives (core/db_release.py)
    part = _path(storage, key).with_name(f"{upload.id}.{offset}.{uuid.uuid4().hex[:8]}.part")
    written = 0
    try:
        with open(part, "wb") as fh:
            async for piece in request.stream():
                written += len(piece)
                if written > CHUNK_BYTES:
                    raise HTTPException(status_code=413, detail=f"A piece is at most {CHUNK_BYTES // (1024 * 1024)} MB.")
                fh.write(piece)
        upload = await _own(session, tenant, upload_id, lock=True)
        if offset + written > upload.size:
            raise HTTPException(status_code=422, detail="This piece goes past the end of the file.")
        if offset > upload.received:
            raise HTTPException(status_code=409, detail={"message": "A piece is missing before this one.",
                                                         "received": upload.received})
        if offset + written > upload.received:  # new bytes (a resent piece that is already here changes nothing)
            skip = upload.received - offset
            await asyncio.to_thread(_append, part, _path(storage, key), skip)
            upload.received = offset + written
            upload.updated_at = _now()
        await session.commit()
    finally:
        part.unlink(missing_ok=True)
    return await _out(session, upload)


def _append(part: Any, target: Any, skip: int) -> None:
    with open(part, "rb") as src, open(target, "ab") as dst:
        src.seek(skip)
        shutil.copyfileobj(src, dst, 1024 * 1024)


@router.post("/psd-uploads/{upload_id}/complete", response_model=UploadOut)
async def complete(
    upload_id: uuid.UUID,
    session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> UploadOut:
    upload = await _own(session, tenant, upload_id, lock=True)
    if upload.status in ("queued", "converting", "ready", "used"):
        return await _out(session, upload)  # pressed twice: nothing more
    if upload.status != "receiving" or not upload.file_key:
        raise HTTPException(status_code=409, detail="This upload cannot be finished; upload the file again.")
    if upload.received != upload.size:
        raise HTTPException(status_code=409, detail={"message": "Not all of the file has arrived yet.",
                                                     "received": upload.received})
    with open(_path(storage, upload.file_key), "rb") as fh:
        if fh.read(4) != b"8BPS":
            upload.status, upload.error, upload.finished_at = "failed", "This is not a Photoshop file. " + NOT_PSD, _now()
            await session.commit()
            return await _out(session, upload)
    storage_cap.added(tenant.id, upload.size)
    upload.status, upload.updated_at = "queued", _now()
    await session.commit()
    await enqueuer.enqueue("convert_psd", str(upload.id), _job_id=f"psd:{upload.id}:{uuid.uuid4().hex[:8]}")
    return await _out(session, upload)


@router.post("/psd-uploads/{upload_id}/choice", response_model=UploadOut)
async def choose(
    upload_id: uuid.UUID,
    body: ChoiceIn,
    session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> UploadOut:
    """The design goes in another smart object: converted again from the kept PSD."""
    upload = await _own(session, tenant, upload_id, lock=True)
    if upload.status != "ready":
        raise HTTPException(status_code=409, detail="Wait until the template is ready.")
    if not upload.file_key:
        raise HTTPException(status_code=409, detail=NOT_KEPT)
    photo = await session.get(MockupPhoto, upload.photo_id) if upload.photo_id else None
    options = (photo.psd or {}).get("options", []) if photo else []
    if not any(o["index"] == body.index and o.get("usable", True) for o in options):
        raise HTTPException(status_code=422, detail="That smart object cannot hold a design.")
    if body.index == upload.choice:
        return await _out(session, upload)
    upload.choice, upload.status, upload.updated_at = body.index, "queued", _now()
    await session.commit()
    await enqueuer.enqueue("convert_psd", str(upload.id), _job_id=f"psd:{upload.id}:{uuid.uuid4().hex[:8]}")
    return await _out(session, upload)


@router.get("/psd-uploads/{upload_id}/preview")
async def preview(
    upload_id: uuid.UUID,
    request: Request,
    v: str | None = Query(None, max_length=40),
    size: str | None = Query(None),
    session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> Response:
    """The template with our sample design placed (api/images.py: versioned, sized)."""
    upload = await _own(session, tenant, upload_id)
    photo = await session.get(MockupPhoto, upload.photo_id) if upload.photo_id else None
    key = (photo.psd or {}).get("preview") if photo is not None else None
    if not key:
        raise HTTPException(status_code=404, detail="no preview yet")
    return await images.send_stored(request, storage, key=key, url_version=str(photo.version), asked=v,
                                    size=images.size_of(size, None))


@router.post("/psd-uploads/{upload_id}/use", response_model=UploadOut)
async def use(
    upload_id: uuid.UUID,
    session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> UploadOut:
    """"Use this template": its photo is confirmed (ready for mockups) and the PSD is deleted."""
    upload = await _own(session, tenant, upload_id, lock=True)
    if upload.status == "used":
        return await _out(session, upload)
    if upload.status != "ready" or not upload.photo_id:
        raise HTTPException(status_code=409, detail="Wait until the template is ready.")
    photo = await session.get(MockupPhoto, upload.photo_id)
    if photo is None:
        raise HTTPException(status_code=409, detail="This template was deleted; upload the file again.")
    photo.confirmed_at = _now()
    key, upload.file_key = upload.file_key, None
    upload.status, upload.updated_at = "used", _now()
    audit.record(session, "psd_upload.used", actor=tenant, target_tenant_id=tenant.id, by="seller", object_id=str(upload.id))
    await session.commit()
    if key:
        await asyncio.to_thread(psd_work._delete_file, storage, key)
    return await _out(session, upload)


@router.delete("/psd-uploads/{upload_id}", status_code=204)
async def cancel(
    upload_id: uuid.UUID,
    session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> None:
    """Stop and forget: the PSD goes, and the template made from it unless it was already used."""
    upload = await _own(session, tenant, upload_id, lock=True)
    if upload.status == "converting":
        raise HTTPException(status_code=409, detail="It is converting now; cancel it in a moment.")
    key, upload.file_key = upload.file_key, None
    prefix = None
    if upload.status != "used" and upload.template_id:
        template = await session.get(MockupTemplate, upload.template_id)
        photos = await session.scalar(select(func.count()).select_from(MockupPhoto).where(MockupPhoto.template_id == upload.template_id))
        if template is not None and int(photos or 0) <= 1:
            prefix = f"{tenant.id}/templates/{upload.photo_id}" if upload.photo_id else None
            await session.delete(template)
            audit.destructive(session, "mockup_template.deleted", actor=tenant, tenant_id=tenant.id, shop_id=None,
                              object_id=template.id, photos=int(photos or 0), from_psd=True)
    upload.status, upload.finished_at, upload.updated_at = "cancelled", _now(), _now()
    await session.commit()
    if key:
        await asyncio.to_thread(psd_work._delete_file, storage, key)
    if prefix:
        await asyncio.to_thread(storage.delete_prefix, prefix)
    return None


def chunk_limit() -> int:
    """The body limit of a piece (core/security.py)."""
    return CHUNK_BYTES + 64 * 1024


