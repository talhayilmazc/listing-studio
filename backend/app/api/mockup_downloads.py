"""Mockup downloads (feature/mockups-visible, A2; pipeline/mockup_download.py).

- ``GET/PUT /mockup-download-options``: the seller's format, size, quality, names and ZIP folders, remembered.
- ``GET /mockup-runs/{run}/outputs/{output}/download``: one mockup, made in the request (one image of at most
  2,000 px, on the image threads) and sent as an attachment.
- ``GET /mockup-zips``: the Downloads tab: the account's ZIPs of the last 48 hours, being made or ready.

ZIPs themselves (a run, a design, "Download selected") are asked for with ``POST /mockup-runs/{run}/zip`` and
made in the worker (api/mockups.py, workers/mockups.py ``build_zip``). Everything is the account's own; another
account's run or mockup is 404.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import active_tenant, get_session, get_storage
from app.api.mockups import ZipOut, _own_run, _zip_out
from app.db.models import MockupDesign, MockupOutput, MockupRun, MockupZip, Tenant, UploadBatch
from app.pipeline import mockup_download as dl
from app.pipeline.batch_names import names_for
from app.pipeline.storage import Storage
from app.workers import mockups as work

router = APIRouter(prefix="/api", tags=["mockups"])


class DownloadOptions(BaseModel):
    format: Literal["jpg", "png", "webp"] = "jpg"
    size: Literal["original", "3000", "2000", "1080"] = "original"
    quality: Literal[92, 85] = 92
    naming: Literal["full", "sku", "file"] = "full"
    layout: Literal["folders", "flat"] = "folders"


@router.get("/mockup-download-options", response_model=DownloadOptions)
async def get_options(tenant: Tenant = Depends(active_tenant)) -> DownloadOptions:
    return DownloadOptions(**dl.Options.of(tenant.mockup_download).as_dict())


@router.put("/mockup-download-options", response_model=DownloadOptions)
async def save_options(
    body: DownloadOptions, session: AsyncSession = Depends(get_session, scope="function"), tenant: Tenant = Depends(active_tenant),
) -> DownloadOptions:
    tenant.mockup_download = dl.Options.of(body.model_dump()).as_dict()
    await session.commit()
    return DownloadOptions(**tenant.mockup_download)


@router.get("/mockup-runs/{run_id}/outputs/{output_id}/download")
async def download_one(
    run_id: uuid.UUID,
    output_id: uuid.UUID,
    format: Literal["jpg", "png", "webp"] | None = Query(None),  # noqa: A002 - the option's name
    size: Literal["original", "3000", "2000", "1080"] | None = Query(None),
    quality: Literal["92", "85"] | None = Query(None),
    naming: Literal["full", "sku", "file"] | None = Query(None),
    session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> Response:
    """One mockup in the seller's options (each given here wins over the saved one): the pixels a draft sends,
    as an attachment named as the ZIP would name it."""
    run = await _own_run(session, tenant, run_id)
    asked = {k: v for k, v in {"format": format, "size": size, "quality": quality, "naming": naming}.items() if v is not None}
    options = dl.Options.of({**(tenant.mockup_download or {}), **asked})
    entries = await work.download_entries(session, run, output_ids=[str(output_id)])
    if not entries:
        known = await session.get(MockupOutput, output_id)
        if known is None or known.run_id != run.id or known.tenant_id != tenant.id:
            raise HTTPException(status_code=404, detail="mockup not found")
        raise HTTPException(status_code=410, detail="this mockup's file has been removed")
    entry = entries[0]
    await session.commit()  # nothing held while the image is made (core/db_release.py)
    try:
        body = await work.download_bytes(storage, entry, options)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=410, detail="this mockup's file has been removed") from exc
    name = dl.names([entry.item], options)[0]
    return Response(body, media_type=dl.MIME[options.format], headers={
        "Content-Disposition": f"attachment; filename=\"{name.encode('ascii', 'replace').decode().replace('?', '_')}\"; "
                               f"filename*=UTF-8''{quote(name)}",
        "Cache-Control": "private, no-store",
    })


@router.get("/mockup-zips", response_model=list[ZipOut])
async def list_zips(
    session: AsyncSession = Depends(get_session, scope="function"), tenant: Tenant = Depends(active_tenant),
) -> list[ZipOut]:
    """The Downloads tab: this account's ZIPs of the last 48 hours (a ready one is kept that long), newest first."""
    since = datetime.now(timezone.utc) - timedelta(hours=MockupZip.RETENTION_HOURS)
    zips = list((await session.execute(select(MockupZip).where(
        MockupZip.tenant_id == tenant.id, MockupZip.created_at >= since).order_by(MockupZip.created_at.desc()).limit(100))).scalars())
    runs = {r.id: r for r in (await session.execute(select(MockupRun).where(
        MockupRun.id.in_({z.run_id for z in zips})))).scalars()} if zips else {}
    batches = {b.id: b for b in (await session.execute(select(UploadBatch).where(
        UploadBatch.id.in_({r.batch_id for r in runs.values()})))).scalars()} if runs else {}
    batch_names = await names_for(session, list(batches.values())) if batches else {}
    designs = dict((await session.execute(select(MockupDesign.batch_id, func.count()).where(
        MockupDesign.batch_id.in_(set(batches))).group_by(MockupDesign.batch_id))).all()) if batches else {}
    out = []
    for z in zips:
        run = runs.get(z.run_id)
        name = batch_names.get(run.batch_id) if run is not None else None
        if z.output_ids is not None:
            what = f"{len(z.output_ids)} {'mockup' if len(z.output_ids) == 1 else 'mockups'}"
        else:
            n = len(z.design_ids) if z.design_ids is not None else (designs.get(run.batch_id, 0) if run is not None else 0)
            what = f"{n} {'design' if n == 1 else 'designs'}"
        out.append(_zip_out(z, label=" · ".join(p for p in (name, what) if p)))
    return out
