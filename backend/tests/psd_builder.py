"""Synthetic Photoshop files for the PSD template tests (feature/psd-templates). No customer file is in the repo.

``build(path, size, layers)`` writes a real PSD with psd-tools: pixel layers, and smart-object layers that carry
what Photoshop writes for a placed, embedded image: the ``SoLd`` descriptor (identity, the four corners on the
page, the warp, the embedded canvas size, smart filters) and the embedded PNG in the global ``lnk2`` block. A
smart object's own pixels are its content drawn into its quad (a perspective map; a mesh warp is declared and
its pixels are drawn with the same bezier patch the converter uses), so the file's composite looks as Photoshop
would show it.
"""

from __future__ import annotations

import io
import uuid as uuidlib
from dataclasses import dataclass, field
from typing import Any

from PIL import Image


@dataclass
class Pixel:
    image: Image.Image  # RGBA
    name: str = "Layer"
    top: int = 0
    left: int = 0
    blend: str = "NORMAL"
    opacity: int = 255


@dataclass
class Smart:
    content: Image.Image  # the embedded design, RGBA
    quad: list[tuple[float, float]]  # TL, TR, BR, BL on the page
    name: str = "Your design"
    blend: str = "NORMAL"
    opacity: int = 255
    mesh: list[tuple[float, float]] | None = None  # 16 points on the page (a 4 x 4 bezier patch)
    style: str | None = None  # a standard warp style, e.g. "warpArc"
    filters: list[str] = field(default_factory=list)  # smart filter names, e.g. "Displace"
    mask: Image.Image | None = None  # "L", page size: a layer mask
    linked: bool = False  # a linked (external) smart object: its content is not in the file


def _png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _perspective(content: Image.Image, quad: list[tuple[float, float]], size: tuple[int, int]) -> Image.Image:
    """``content`` drawn into ``quad`` on a transparent page of ``size``."""
    import numpy as np

    w, h = content.size
    src = [(0, 0), (w, 0), (w, h), (0, h)]
    # The page-to-content homography: solve for the 8 coefficients PIL wants.
    rows, rhs = [], []
    for (x, y), (u, v) in zip(quad, src):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); rhs.append(u)
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y]); rhs.append(v)
    coeffs = np.linalg.solve(np.array(rows, float), np.array(rhs, float))
    return content.transform(size, Image.PERSPECTIVE, tuple(coeffs), Image.BICUBIC)


def _drawn(spec: "Smart", size: tuple[int, int]) -> Image.Image:
    """The smart object's pixels as Photoshop shows them: its content through its corners. A bent mesh is drawn
    with the converter's own bezier patch (pipeline/psd_template.py), so that case checks consistency, not
    Photoshop; the others use Pillow's perspective map, independent of our renderer."""
    if not spec.mesh:
        return _perspective(spec.content.convert("RGBA"), spec.quad, size)
    import numpy as np

    from app.pipeline import psd_template

    w, h = spec.content.size
    mesh = [(x / w, y / h) for x, y in spec.mesh]
    placed, left, top = psd_template.warp(np.asarray(spec.content.convert("RGBA")), psd_template.Spec(
        width=size[0], height=size[1], canvas=(w, h), quad=spec.quad, mesh=mesh))
    page = np.zeros((size[1], size[0], 4), np.float32)
    ph, pw = placed.shape[:2]
    page[top:top + ph, left:left + pw] = placed
    alpha = page[..., 3:4]
    rgb = np.where(alpha > 0, page[..., :3] / np.maximum(alpha, 1e-6) * 255.0, 0)
    out = np.concatenate([np.clip(rgb, 0, 255), np.clip(alpha, 0, 255)], axis=2)
    return Image.fromarray((out + 0.5).astype(np.uint8), "RGBA")


def _descriptor(smart: Smart, ident: str, size: tuple[int, int]) -> Any:
    from psd_tools.psd.descriptor import (Descriptor, DescriptorBlock, Double, Enumerated, Integer, List,
                                          ObjectArray, String, UnitFloat, UnitFloats)
    from psd_tools.terminology import Unit

    w, h = smart.content.size
    corners = List([Double(v) for p in smart.quad for v in p])
    warp = Descriptor(classID=b"warp")
    style = b"warpCustom" if smart.mesh else (smart.style or "warpNone").encode()
    warp[b"warpStyle"] = Enumerated(b"warpStyle", style)
    warp[b"warpValue"] = Double(0.0)
    warp[b"warpPerspective"] = Double(0.0)
    warp[b"warpPerspectiveOther"] = Double(0.0)
    warp[b"warpRotate"] = Enumerated(b"Ornt", b"Hrzn")
    bounds = Descriptor(classID=b"classFloatRect")
    for key, value in ((b"Top ", 0.0), (b"Left", 0.0), (b"Btom", float(h)), (b"Rght", float(w))):
        bounds[key] = UnitFloat(value, Unit.Pixels)
    warp[b"bounds"] = bounds
    warp[b"uOrder"] = Integer(4)
    warp[b"vOrder"] = Integer(4)
    if smart.mesh:
        custom = Descriptor(classID=b"customEnvelopeWarp")
        points = ObjectArray(items_count=16, classID=b"rationalPoint")
        points[b"Hrzn"] = UnitFloats(Unit.Pixels, [float(x) for x, _ in smart.mesh])
        points[b"Vrtc"] = UnitFloats(Unit.Pixels, [float(y) for _, y in smart.mesh])
        custom[b"meshPoints"] = points
        warp[b"customEnvelopeWarp"] = custom
    block = DescriptorBlock(classID=b"null")
    block[b"Idnt"] = String(ident)
    block[b"placed"] = String(ident)
    block[b"PgNm"] = Integer(1)
    block[b"totalPages"] = Integer(1)
    block[b"Type"] = Integer(2)
    block[b"Trnf"] = corners
    block[b"nonAffineTransform"] = List([Double(v) for p in smart.quad for v in p])
    block[b"warp"] = warp
    size_d = Descriptor(classID=b"Pnt ")
    size_d[b"Wdth"] = Double(float(w))
    size_d[b"Hght"] = Double(float(h))
    block[b"Sz  "] = size_d
    block[b"Rslt"] = UnitFloat(72.0, Unit.Density)
    if smart.filters:
        fx = Descriptor(classID=b"filterFXStyle")
        items = []
        for name in smart.filters:
            item = Descriptor(classID=b"filterFX")
            item[b"Nm  "] = String(name + "...")
            item[b"Fltr"] = Descriptor(classID=name[:4].encode().ljust(4))
            items.append(item)
        fx[b"filterFXList"] = List(items)
        block[b"filterFX"] = fx
    return block


def build(path: str, size: tuple[int, int], layers: list[Pixel | Smart], *, background: tuple = (240, 240, 240)) -> None:
    """Write a PSD of ``size`` with a background and ``layers`` bottom to top."""
    from psd_tools import PSDImage
    from psd_tools.api.layers import PixelLayer
    from psd_tools.constants import BlendMode, LinkedLayerType, Tag
    from psd_tools.psd.linked_layer import LinkedLayer
    from psd_tools.psd.tagged_blocks import TaggedBlocks

    # An RGBA document: psd-tools then keeps a layer's alpha as its transparency, as Photoshop does (in an RGB
    # document frompil turns alpha into a layer mask).
    psd = PSDImage.new("RGBA", size, color=background + (255,))
    back = PixelLayer.frompil(Image.new("RGBA", size, background + (255,)), psd, "Background")
    psd.append(back)
    linked = []
    for spec in layers:
        if isinstance(spec, Pixel):
            layer = PixelLayer.frompil(spec.image.convert("RGBA"), psd, spec.name, spec.top, spec.left)
        else:
            page = _drawn(spec, size)
            box = page.getbbox() or (0, 0, 1, 1)
            layer = PixelLayer.frompil(page.crop(box), psd, spec.name, box[1], box[0])
            ident = str(uuidlib.uuid4())
            layer.tagged_blocks.set_data(Tag.SMART_OBJECT_LAYER_DATA1, data=_descriptor(spec, ident, size))
            if not spec.linked:
                data = _png(spec.content)
                linked.append(LinkedLayer(kind=LinkedLayerType.DATA, version=7, uuid=ident, filename="design.png",
                                          filetype=b"png ", creator=b"8BIM", filesize=len(data), data=data,
                                          child_id="", mod_time=0.0, lock_state=0))
        if isinstance(spec, Smart) and spec.mask is not None:
            layer.create_mask(spec.mask.convert("L"), 0, 0)
        layer.blend_mode = BlendMode[spec.blend]
        layer.opacity = spec.opacity
        psd.append(layer)
    if linked:
        info = psd._record.layer_and_mask_information
        if info.tagged_blocks is None:
            info.tagged_blocks = TaggedBlocks()
        info.tagged_blocks.set_data(Tag.LINKED_LAYER2, linked)
    psd.save(path)
