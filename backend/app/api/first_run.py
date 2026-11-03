"""The first run: a sample design, a sample mockup template and a finished draft preview (``pipeline/first_run.py``).

Read only. Nothing here writes a row, queues a job, calls a model or reaches Etsy, so the path needs no shop and no
allowance. The sample never goes to a shop: the seller's first real draft is their own design, with their approval.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from app.api import images
from app.api.deps import active_tenant
from app.core import image_work
from app.db.models import Tenant
from app.pipeline import first_run as sample

router = APIRouter(prefix="/api/first-run", tags=["first-run"])


class Attribute(BaseModel):
    name: str
    value: str


class SampleOut(BaseModel):
    version: str
    title: str
    tags: list[str]
    description: str
    materials: list[str]
    price: str
    currency: str
    attributes: list[Attribute]
    section: str
    colours: list[str]
    #: The print area on the blank shirt: top left, top right, bottom right, bottom left, as shares of the picture.
    area: list[list[float]]


@router.get("", response_model=SampleOut)
async def first_run(_: Tenant = Depends(active_tenant)) -> SampleOut:
    return SampleOut(version=sample.VERSION, colours=list(sample.COLOURS), area=sample.AREA, **sample.TEXT)


@router.get("/image")
async def first_run_image(request: Request, name: str, v: str | None = None, _: Tenant = Depends(active_tenant)) -> Response:
    if name not in sample.IMAGES:
        raise HTTPException(status_code=404, detail="No such picture.")
    data, media_type = await image_work.run(sample.image, name)
    return await images.send(request, tag=images.etag(f"first-run-{sample.VERSION}-{name}", None, None), versioned=v == sample.VERSION,
                             media_type=media_type, load=lambda: data)
