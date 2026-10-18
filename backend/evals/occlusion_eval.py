"""Occlusion false positives on the seller's own blank photos (Part B4).

Run as the other mockup evaluations (``evals/mockup_eval.py``; photos read-only, output
outside the repo): ``python -m evals.occlusion_eval bench <tag> [dev|holdout|all]`` and
``python -m evals.occlusion_eval compare <before> <after>``.

For every marked print area (the frozen answer key, ``evals/geometry_eval.py``) it
measures what the occluder the app computes for the photo would hide there: the share
of the area, and how many visible pieces stay. The detector runs exactly as the worker
runs it, so ``bench before`` on the code before the guards and ``bench after`` on the
code with them compare like with like.

Counted as a false positive: any hidden part (over 1% of the area) on a photo with no
person (flat-lay, folded, hanger, several garments: nothing can be in front of the print
there); and on a worn photo, an area more than 25% hidden or cut into several pieces
(hair and arms rarely cover that much of a chest print; each such photo is listed for a
look, ``sheet``). Reported by photo type and by garment colour, read from the photo
inside the area: light neutral (white, grey, heather), beige or skin-toned, dark, other.
"""

from __future__ import annotations

import io
import json
import statistics
import sys
from pathlib import Path
from typing import Any

from PIL import Image

from evals import geometry_eval as ge
from evals import mockup_eval as me

OUT = me.OUT / "occlusion"


def _colour(rgb, area) -> str:  # noqa: ANN001
    import numpy as np
    from skimage.color import rgb2lab

    lab = rgb2lab(rgb[area].reshape(-1, 1, 3) / 255.0).reshape(-1, 3)
    if not len(lab):
        return "other"
    L, a, b = (float(np.median(lab[:, i])) for i in range(3))
    chroma = (a * a + b * b) ** 0.5
    hue = (np.degrees(np.arctan2(b, a)) + 360) % 360
    if L < 35:
        return "dark"
    if chroma < 10 and L >= 50:
        return "light neutral"
    if 8 <= chroma <= 40 and 30 <= hue <= 95 and L >= 45:
        return "beige or skin-toned"
    return "other"


def _one(name: str) -> dict[str, Any]:
    import numpy as np

    from app.pipeline import garments as garments_
    from app.pipeline import mockup_vision as vision
    from app.pipeline import occlusion

    key = ge.answer_key()["photos"][name]
    photo = me.decode(ge._paths()[name])  # noqa: SLF001
    im = Image.open(io.BytesIO(photo.jpeg)).convert("RGB")
    im.thumbnail((garments_.WORK, garments_.WORK))
    rgb = np.asarray(im)
    h, w = rgb.shape[:2]
    found = garments_.detect(rgb, vision.default_runner(), garments_.default_saliency(), name, several=True)
    occ = found.occluder
    corners = [g["area"] for g in key["garments"]]
    masks = [occlusion.area_mask(c, h, w) for c in corners]
    # With the guards (the code has them when the detector reports ``area_checks``), each
    # marked area is judged as the worker judges its areas: blobs wholly inside dropped, and
    # the area's switch off (nothing hidden) past a quarter hidden or several pieces.
    guarded = hasattr(found, "area_checks")
    judged = occlusion.drop_inside_blobs(occ, masks) if guarded else occ
    areas = []
    for m in masks:
        c = occlusion.check_area(judged, m)
        switched_off = guarded and not c.occlusion
        areas.append({"hidden": 0.0 if switched_off else c.hidden, "pieces": 1 if switched_off else c.pieces,
                      "raw_hidden": occlusion.check_area(occ, m).hidden, "switched_off": switched_off,
                      "colour": _colour(rgb, m)})
    return {"name": name, "type": ge.photo_type(me.load_marks()[name]), "persons": found.persons,
            "worn": key.get("layout") == "on a person", "areas": areas}


def _rows(names: list[str], jobs: int) -> list[dict[str, Any]]:
    if jobs <= 1:
        return [_one(n) for n in names]
    import multiprocessing as mp

    with mp.get_context("spawn").Pool(jobs) as pool:
        return pool.map(_one, names)


def _fp(row: dict[str, Any], area: dict[str, Any]) -> bool:
    if not row["worn"]:
        return area["hidden"] > 0.01
    return area["hidden"] > 0.25 or area["pieces"] > 1


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    groups: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for r in rows:
        for a in r["areas"]:
            for g in ("all", "no person" if not r["worn"] else "worn", a["colour"]):
                groups.setdefault(g, []).append((r, a))
    for g, pairs in groups.items():
        out[g] = {
            "areas": len(pairs),
            "false_positive_areas": sum(_fp(r, a) for r, a in pairs),
            "photos_with_false_positive": len({r["name"] for r, a in pairs if _fp(r, a)}),
            "mean_hidden": round(statistics.mean(a["hidden"] for _, a in pairs), 4),
            "areas_switched_off": sum(a["switched_off"] for _, a in pairs),
        }
    return out


def bench(tag: str, which: str = "all", jobs: int = 2) -> None:
    s = ge.split()
    names = s["dev"] + s["holdout"] if which == "all" else s[which]
    rows = _rows(sorted(names), jobs)
    folder = OUT / tag
    folder.mkdir(parents=True, exist_ok=True)
    report = {"which": which, "dev": summary([r for r in rows if r["name"] in s["dev"]]),
              "holdout": summary([r for r in rows if r["name"] in s["holdout"]])}
    (folder / "rows.json").write_text(json.dumps(rows, indent=1))
    (folder / "report.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))
    print("false positives:", [r["name"] for r in rows if any(_fp(r, a) for a in r["areas"])])


def compare(before: str, after: str) -> None:
    b = json.loads((OUT / before / "report.json").read_text())
    a = json.loads((OUT / after / "report.json").read_text())
    for split_ in ("dev", "holdout"):
        print(f"== {split_}")
        for g in sorted(set(b[split_]) | set(a[split_])):
            x, y = b[split_].get(g, {}), a[split_].get(g, {})
            print(f"{g:22s} areas {x.get('areas', 0):3d}  FP areas {x.get('false_positive_areas', 0):3d} -> {y.get('false_positive_areas', 0):3d}"
                  f"  FP photos {x.get('photos_with_false_positive', 0):3d} -> {y.get('photos_with_false_positive', 0):3d}"
                  f"  mean hidden {x.get('mean_hidden', 0):.3f} -> {y.get('mean_hidden', 0):.3f}"
                  f"  switched off {y.get('areas_switched_off', 0)}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "bench":
        jobs = int(args[args.index("--jobs") + 1]) if "--jobs" in args else 2
        rest = [x for x in args[1:] if not x.startswith("--") and not x.isdigit()]
        bench(rest[0], rest[1] if len(rest) > 1 else "all", jobs)
    elif args and args[0] == "compare":
        compare(args[1], args[2])
    else:
        print(__doc__)
