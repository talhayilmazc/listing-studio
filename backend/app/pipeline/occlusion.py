"""What may go in front of the design: guards on the analysis's occluder (Part B4).

A seller's grey tee came out with about half the design hidden, cut into pieces: the
segmenter read parts of the grey fabric as skin or as an "accessory", and a patch the
region step had left out of the garment counted as a held object. These rules decide
what the occluder may contain, from the segmenter's own output only (no new model):

* **Only hair, skin and face, and only where the photo shows a person**, each class
  sure of itself (``CLASS_CONFIDENCE``) and decided against the fabric under it. No
  "accessory" class, no held-object patches, never the garment or the background: on a
  flat-lay, a folded tee or a hanger nothing is in front of the print.
* **An occluder reaches the area from outside it**: hair falls over it, an arm crosses
  it. A blob lying wholly inside a print area is a misread of the fabric and is dropped.
* **Per area, a last check**: if more than ``HIDDEN_MAX`` of the area would be hidden,
  or what stays visible falls into several pieces, the design does not go behind
  anything in that area (its switch is turned off) and the photo asks for the seller's
  check. The seller can turn it back on, and the "Keep in front" brush still adds or
  erases by hand.

Deterministic, numpy and scipy only; nothing leaves the server.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: A pixel is a person's (hair, skin or face) only when the segmenter gives those classes
#: together at least this: the person is the most likely reading of it. (One class at 0.6,
#: tried first on the dev photos, broke a hand holding a mug into pieces: lighter skin is
#: split between the skin and face classes.)
CLASS_CONFIDENCE = 0.5
#: The occluder's soft map counts as "in front" above this (for the shape rules).
ON = 0.5
#: More than this share of a print area hidden: the area's occlusion is turned off.
HIDDEN_MAX = 0.25
#: A visible piece of the area this share of it or larger counts as a piece of the design.
PIECE_MIN = 0.04
#: The fabric decision, as before (mockup_vision.FRONT_RAMP): in front where the person
#: classes outweigh the fabric.
RAMP = (0.40, 0.60)

# The segmenter's classes (mockup_vision): background, hair, body skin, face, clothes, others.
BACKGROUND, HAIR, BODY, FACE, CLOTHES, OTHERS = range(6)


def person_front(probs: np.ndarray, person: bool) -> np.ndarray:
    """What is in front of the garment (float 0..1, (h, w)): hair, skin and face that the
    segmenter is sure of, against the fabric under them; nothing when no person is in
    the photo."""
    h, w = probs.shape[:2]
    if not person:
        return np.zeros((h, w), np.float32)
    hair, body, face, clothes = probs[..., HAIR], probs[..., BODY], probs[..., FACE], probs[..., CLOTHES]
    person_p = hair + body + face
    front = np.where(person_p >= CLASS_CONFIDENCE, person_p, 0.0)
    share = front / (front + clothes + 1e-6)
    lo, hi = RAMP
    t = np.clip((share - lo) / (hi - lo), 0.0, 1.0)
    return (t * t * (3 - 2 * t)).astype(np.float32)


def area_mask(corners: list[list[float]], h: int, w: int) -> np.ndarray:
    """The print area (normalised corners TL, TR, BR, BL) as a boolean mask (h, w)."""
    from skimage.draw import polygon

    rr, cc = polygon([c[1] * h for c in corners], [c[0] * w for c in corners], (h, w))
    out = np.zeros((h, w), bool)
    out[rr, cc] = True
    return out


@dataclass(frozen=True)
class AreaCheck:
    #: The design goes behind what is in front, in this area.
    occlusion: bool
    #: Share of the area hidden, after inside-only blobs are dropped.
    hidden: float
    #: Visible pieces of the area (1 = whole).
    pieces: int
    #: Why it was turned off (the seller reads it); None when on.
    reason: str | None = None


def drop_inside_blobs(occluder: np.ndarray, areas: list[np.ndarray]) -> np.ndarray:
    """``occluder`` without the blobs that lie wholly inside a print area (a blob must
    reach the area from outside it). Soft values are kept elsewhere."""
    from scipy import ndimage as ndi

    on = occluder > ON
    if not on.any() or not areas:
        return occluder
    labels, n = ndi.label(on, structure=np.ones((3, 3), bool))
    keep = occluder.copy()
    for area in areas:
        inside = np.unique(labels[area & on])
        for i in inside[inside > 0]:
            part = labels == i
            if not (part & ~area).any():
                keep[ndi.binary_dilation(part, iterations=2)] = 0.0  # its soft edge too
    return keep


def check_area(occluder: np.ndarray, area: np.ndarray) -> AreaCheck:
    """Whether the design may go behind ``occluder`` in ``area`` (both (h, w))."""
    from scipy import ndimage as ndi

    total = int(area.sum())
    if total == 0:
        return AreaCheck(True, 0.0, 1)
    hidden_px = area & (occluder > ON)
    hidden = float(hidden_px.sum()) / total
    visible = area & ~hidden_px
    labels, n = ndi.label(visible, structure=np.ones((3, 3), bool))
    sizes = np.bincount(labels.ravel())[1:] if n else np.array([])
    pieces = int((sizes >= PIECE_MIN * total).sum())
    if hidden > HIDDEN_MAX:
        return AreaCheck(False, round(hidden, 4), max(pieces, 1),
                         f"{round(hidden * 100)}% of the print area would be hidden; check whether hair or arms are really in front.")
    if pieces > 1:
        return AreaCheck(False, round(hidden, 4), pieces,
                         f"What is in front would cut the design into {pieces} pieces; check whether hair or arms are really in front.")
    return AreaCheck(True, round(hidden, 4), max(pieces, 1))


def guard(occluder: np.ndarray, corners: list[list[list[float]]]) -> tuple[np.ndarray, list[AreaCheck]]:
    """The occluder with inside-only blobs dropped, and each area's check (in order)."""
    h, w = occluder.shape
    areas = [area_mask(c, h, w) for c in corners if c]
    cleaned = drop_inside_blobs(occluder.astype(np.float32), areas)
    return cleaned, [check_area(cleaned, a) for a in areas]
