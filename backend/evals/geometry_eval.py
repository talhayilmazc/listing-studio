"""Print-area geometry, evaluated honestly on the seller's own blank photos.

Everything is read from and written to the folders ``evals/mockup_eval.py`` uses (the
photos read-only, the output outside the repo); nothing here goes into the repo or the
image. Run it the same way (see that module), ``python -m evals.geometry_eval <command>``:

* ``split``: the unique photos split once into dev (~70%) and holdout (~30%), stratified
  by layout, with a fixed seed; written to /out/geometry/split.json and never rewritten
  (``--force`` only on purpose). Tuning looks at dev only.
* ``key``: the answer key frozen once (/out/geometry/answer-key.json): each marked
  garment's print area (the PSD smart object, else the marked landmarks through the rule
  as it was when the key was frozen), its landmarks and chest width in pixels. Changing
  the rule later cannot move the key.
* ``bench <tag> [dev|holdout|all] [--learn] [--jobs N]``: runs the detector on the split's
  photos and writes /out/geometry/<tag>/rows.json, detections.json and report.txt. With
  ``--learn`` it also simulates a seller's corrections: in each layout, the first
  LEARN_FIRST photos (by name) are confirmed with the key's area; the seller's own
  ratios (``print_rule.observe`` / ``personal_table``) then place the rest, which are
  scored with and without them.
* ``compare <before> <after>``: the two reports side by side.

Metrics (unique photos; black-on-black / white-on-white reported apart): IoU >= 0.8 and
median IoU per area; photos whose every area reaches 0.8; zero-action photos (every
garment found with IoU >= 0.9 and no extra area: "Looks right" is all it needs); areas
outside the garment (more than OUTSIDE_LIMIT of the area off the foreground, measured on
BiRefNet masks computed for the evaluation only, or centred on no marked garment);
centre error and width error as a share of the marked chest width; angle error in
degrees (all areas and the tilted ones: key angle >= 6 degrees); areas reaching the
collar (above the marked collar point, or a hoodie's marked ``top``).
"""

from __future__ import annotations

import inspect
import io
import json
import math
import random
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from evals import mockup_eval as me

OUT = me.OUT / "geometry"
SEED = 20261016
HOLDOUT_SHARE = 0.30
OUTSIDE_LIMIT = 0.05
TILTED = 6.0
LEARN_FIRST = 3
GROUPS = {"on a person": "worn", "folded": "folded", "flat-lay": "flat-lay", "on a hanger": "hanger",
          "several garments": "several"}


def _raw() -> dict[str, Any]:
    raw = json.loads(me.MARKS.read_text(encoding="utf-8"))
    return raw.get("photos", raw)


def _paths() -> dict[str, Path]:
    return {p.name: p for p in me.photos()}


# --- split and key -----------------------------------------------------------------------------


def split(force: bool = False) -> dict[str, Any]:
    path = OUT / "split.json"
    if path.exists() and not force:
        return json.loads(path.read_text())
    raw, files = _raw(), _paths()
    unique = sorted(n for n, e in raw.items() if "same_as" not in e and n in files)
    by_layout: dict[str, list[str]] = {}
    for n in unique:
        by_layout.setdefault(GROUPS.get(raw[n].get("layout"), "other"), []).append(n)
    rng = random.Random(SEED)
    dev, holdout, counts = [], [], {}
    for layout in sorted(by_layout):
        items = sorted(by_layout[layout])
        rng.shuffle(items)
        k = round(HOLDOUT_SHARE * len(items)) if len(items) >= 2 else 0
        holdout += items[:k]
        dev += items[k:]
        counts[layout] = {"dev": len(items) - k, "holdout": k}
    data = {"seed": SEED, "holdout_share": HOLDOUT_SHARE, "made": time.strftime("%Y-%m-%d"),
            "by_layout": counts, "dev": sorted(dev), "holdout": sorted(holdout)}
    OUT.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1))
    return data


def _edge_angle(q: list[list[float]], w: int, h: int) -> float:
    return math.degrees(math.atan2((q[1][1] - q[0][1]) * h, (q[1][0] - q[0][0]) * w))


def answer_key(force: bool = False) -> dict[str, Any]:
    path = OUT / "answer-key.json"
    if path.exists() and not force:
        return json.loads(path.read_text())
    s = split()
    marks, files = me.load_marks(), _paths()
    photos_: dict[str, Any] = {}
    for name in s["dev"] + s["holdout"]:
        entry = marks[name]
        photo = me.decode(files[name])
        w, h = photo.width, photo.height
        areas = me.marked_areas(entry, photo)
        gs = []
        for g, area in zip(entry["garments"], areas):
            groups = me.garment_groups(entry, g)
            (ax, ay), (bx, by) = g["armpits"]
            gs.append({"area": area, "neck": g["neck"], "armpits": g["armpits"], "top": g.get("top"),
                       "bottom": g.get("bottom"), "chest_px": math.dist((ax * w, ay * h), (bx * w, by * h)),
                       "angle": round(_edge_angle(area, w, h), 2), "layout": groups["layout"],
                       "type": groups["type"], "low_contrast": groups["low_contrast"]})
        # Garments the marks leave out on purpose (another garment lies over their chest):
        # an area on one is not "outside the garment".
        unmarked = bool(entry.get("dropped_garments") or entry.get("dropped")
                        or re.search(r"\b(is|are) dropped", (entry.get("notes") or "").lower()))
        photos_[name] = {"width": w, "height": h, "layout": entry.get("layout"), "type": entry.get("type"),
                         "unmarked_garments": unmarked,
                         "psd_quad": bool(me.psd_quad(name, entry, photo) and len(entry["garments"]) == 1),
                         "garments": gs}
    from app.pipeline import print_rule

    data = {"frozen": time.strftime("%Y-%m-%d"), "standard": print_rule.STANDARD, "photos": photos_}
    path.write_text(json.dumps(data, indent=1))
    return data


# --- detection ---------------------------------------------------------------------------------


def _detect(name: str, table: dict[str, Any] | None = None) -> dict[str, Any]:
    """The app's detector on one photo (as the worker runs it): areas and landmarks,
    normalised to the photo."""
    import numpy as np

    from app.pipeline import garments as garments_
    from app.pipeline import mockup_vision as vision

    photo = me.decode(_paths()[name])
    im = Image.open(io.BytesIO(photo.jpeg)).convert("RGB")
    im.thumbnail((garments_.WORK, garments_.WORK))
    rgb = np.asarray(im)
    aw, ah = im.size
    kwargs = {}
    if table is not None and "personal" in inspect.signature(garments_.detect).parameters:
        kwargs["personal"] = table
    t = time.perf_counter()
    found = garments_.detect(rgb, vision.default_runner(), garments_.default_saliency(), name, **kwargs)
    seconds = time.perf_counter() - t
    nx = lambda p: [round(p[0] / aw, 5), round(p[1] / ah, 5)]  # noqa: E731
    out = []
    for g in found.garments:
        out.append({"area": [nx(p) for p in g.corners], "neck": nx(g.neck), "armpits": [nx(p) for p in g.armpits],
                    "bottom": nx(g.bottom) if g.bottom else None, "kind": g.kind, "view": g.view,
                    "layout": g.layout, "confidence": round(float(g.confidence), 3),
                    "angle": g.info.get("angle"), "guards": g.info.get("guards", []),
                    "notes": list(g.notes)})
    return {"name": name, "seconds": round(seconds, 2), "garments": out}


def _detect_many(names: list[str], jobs: int, tables: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    args = [(n, (tables or {}).get(n)) for n in names]
    if jobs <= 1:
        return {n: _detect(n, t) for n, t in args}
    import multiprocessing as mp

    with mp.get_context("spawn").Pool(jobs) as pool:
        return {r["name"]: r for r in pool.starmap(_detect, args)}


# --- scoring -----------------------------------------------------------------------------------


def _fg(name: str, longest: int = 500):  # noqa: ANN202
    import numpy as np

    path = me.OUT / "masks" / "fg" / f"{name}.png"
    if not path.exists():
        return None
    im = Image.open(path).convert("L")
    im.thumbnail((longest, longest))
    return np.asarray(im) > 127


def _outside_share(q: list[list[float]], fg) -> float:  # noqa: ANN001
    import numpy as np

    if fg is None:
        return float("nan")
    h, w = fg.shape
    im = Image.new("1", (w, h), 0)
    ImageDraw.Draw(im).polygon([(x * w, y * h) for x, y in q], fill=1)
    m = np.asarray(im, dtype=bool)
    n = m.sum()
    # off the photo counts as outside too
    full = abs(_shoelace([(x * w, y * h) for x, y in q]))
    if not full:
        return 1.0
    return float(1.0 - (m & fg).sum() / max(full, n))


def _shoelace(pts: list[tuple[float, float]]) -> float:
    return 0.5 * sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))


def _key_frame(kg: dict[str, Any], w: int, h: int):  # noqa: ANN202
    """(across, down, neck px, chest px) of a key garment, along its key area."""
    q = kg["area"]
    ax, ay = (q[1][0] - q[0][0]) * w, (q[1][1] - q[0][1]) * h
    n = math.hypot(ax, ay) or 1.0
    across = (ax / n, ay / n)
    down = (-across[1], across[0])
    (lx, ly), (rx, ry) = kg["armpits"]
    mid = ((lx + rx) / 2 * w, (ly + ry) / 2 * h)
    neck = (kg["neck"][0] * w, kg["neck"][1] * h)
    # the axis through the armpits' midpoint, at the collar's depth
    along = (neck[0] - mid[0]) * across[0] + (neck[1] - mid[1]) * across[1]
    origin = (neck[0] - across[0] * along, neck[1] - across[1] * along)
    return across, down, origin, kg["chest_px"]


def _uv(p: tuple[float, float], frame) -> tuple[float, float]:  # noqa: ANN001
    across, down, origin, _ = frame
    dx, dy = p[0] - origin[0], p[1] - origin[1]
    return dx * across[0] + dy * across[1], dx * down[0] + dy * down[1]


def _on_garment(q: list[list[float]], key: dict[str, Any]) -> int | None:
    """The key garment whose torso holds the area's centre (or None)."""
    w, h = key["width"], key["height"]
    c = (sum(p[0] for p in q) / 4 * w, sum(p[1] for p in q) / 4 * h)
    best = None
    for i, kg in enumerate(key["garments"]):
        fr = _key_frame(kg, w, h)
        u, v = _uv(c, fr)
        chest = fr[3]
        if abs(u) <= 0.6 * chest and -0.15 * chest <= v <= 1.8 * chest:
            if best is None or abs(u) < best[0]:
                best = (abs(u), i)
    return None if best is None else best[1]


def _collar(q: list[list[float]], key: dict[str, Any], index: int | None) -> bool:
    """Whether the area's top reaches above the garment's collar point (or a hoodie's top)."""
    if index is None:
        return False
    w, h = key["width"], key["height"]
    kg = key["garments"][index]
    fr = _key_frame(kg, w, h)
    limit = 0.0
    if kg.get("top"):
        limit = max(limit, _uv((kg["top"][0] * w, kg["top"][1] * h), fr)[1])
    return any(_uv((x * w, y * h), fr)[1] < limit - 1.0 for x, y in q[:2])


def score_photo(name: str, key: dict[str, Any], det: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    w, h = key["width"], key["height"]
    aspect = h / w
    found = [g["area"] for g in det["garments"]]
    fg = _fg(name)
    used: set[int] = set()
    rows = []
    for i, kg in enumerate(key["garments"]):
        mark = kg["area"]
        best, best_iou = None, 0.0
        for j, a in enumerate(found):
            if j in used:
                continue
            v = me.iou(mark, a, aspect)
            if v > best_iou:
                best, best_iou = j, v
        row: dict[str, Any] = {"photo": name, "garment": i, "iou": round(best_iou, 3), "match": best,
                               "layout": GROUPS.get(kg["layout"], kg["layout"]), "type": kg["type"],
                               "low_contrast": kg["low_contrast"], "key_angle": kg["angle"],
                               "tilted": abs(kg["angle"]) >= TILTED, "psd": key["psd_quad"]}
        if best is not None and best_iou > 0.0:
            used.add(best)
            a = found[best]
            chest = kg["chest_px"]
            mc = (sum(p[0] for p in mark) / 4 * w, sum(p[1] for p in mark) / 4 * h)
            fc = (sum(p[0] for p in a) / 4 * w, sum(p[1] for p in a) / 4 * h)
            mw = math.dist((mark[0][0] * w, mark[0][1] * h), (mark[1][0] * w, mark[1][1] * h))
            fw = math.dist((a[0][0] * w, a[0][1] * h), (a[1][0] * w, a[1][1] * h))
            fr = _key_frame(kg, w, h)
            du, dv = _uv(fc, fr)[0] - _uv(mc, fr)[0], _uv(fc, fr)[1] - _uv(mc, fr)[1]
            d = _edge_angle(a, w, h) - kg["angle"]
            g = det["garments"][best]
            dn = _uv((g["neck"][0] * w, g["neck"][1] * h), fr)
            dchest = math.dist((g["armpits"][0][0] * w, g["armpits"][0][1] * h), (g["armpits"][1][0] * w, g["armpits"][1][1] * h))
            darm = _uv(((g["armpits"][0][0] + g["armpits"][1][0]) / 2 * w, (g["armpits"][0][1] + g["armpits"][1][1]) / 2 * h), fr)
            kam = _uv(((kg["armpits"][0][0] + kg["armpits"][1][0]) / 2 * w, (kg["armpits"][0][1] + kg["armpits"][1][1]) / 2 * h), fr)
            row.update({"neck_du": round(dn[0] / chest, 3), "neck_dv": round(dn[1] / chest, 3),
                        "chest_ratio": round(dchest / chest, 3), "armpit_dv": round((darm[1] - kam[1]) / chest, 3)})
            row.update({"centre_error": round(math.dist(mc, fc) / chest, 3), "du": round(du / chest, 3),
                        "dv": round(dv / chest, 3), "width_ratio": round(fw / max(mw, 1.0), 3),
                        "size_error": round(abs(fw / max(mw, 1.0) - 1), 3),
                        "angle_error": round(abs((d + 180) % 360 - 180), 2),
                        "kind": det["garments"][best]["kind"], "found_layout": det["garments"][best]["layout"],
                        "confidence": det["garments"][best]["confidence"]})
        rows.append(row)
    areas = []
    for j, a in enumerate(found):
        on = _on_garment(a, key)
        share = _outside_share(a, fg)
        off_fg = bool(share == share and share > OUTSIDE_LIMIT)
        areas.append({"photo": name, "area": j, "outside_fg": round(share, 3), "on_garment": on,
                      "unmarked": on is None and key.get("unmarked_garments", False) and not off_fg,
                      "outside": bool(off_fg or (on is None and not key.get("unmarked_garments", False))),
                      "collar": _collar(a, key, on),
                      "confidence": det["garments"][j]["confidence"]})
    zero = len(found) == len(key["garments"]) and all(r["iou"] >= me.ACTIONS_IOU for r in rows)
    photo = {"photo": name, "found": len(found), "marked": len(key["garments"]), "zero_action": zero,
             "every_08": all(r["iou"] >= 0.8 for r in rows), "mean_iou": round(statistics.mean(r["iou"] for r in rows), 3),
             "layout": GROUPS.get(key["layout"], key["layout"]), "type": key["type"], "seconds": det["seconds"],
             "low_contrast": all(r["low_contrast"] for r in rows), "areas": areas,
             "min_confidence": min((g["confidence"] for g in det["garments"]), default=0.0)}
    return rows, photo


# --- reports -----------------------------------------------------------------------------------


def _med(vals: list[float]) -> float:
    vals = [v for v in vals if v is not None and v == v]
    return statistics.median(vals) if vals else float("nan")


def _block(rows: list[dict[str, Any]], photos_: list[dict[str, Any]]) -> dict[str, Any]:
    areas = [a for p in photos_ for a in p["areas"]]
    tilted = [r for r in rows if r["tilted"] and r.get("angle_error") is not None]
    return {
        "photos": len(photos_), "garments": len(rows),
        "iou08": sum(r["iou"] >= 0.8 for r in rows) / len(rows) if rows else float("nan"),
        "median_iou": _med([r["iou"] for r in rows]),
        "every08": sum(p["every_08"] for p in photos_) / len(photos_) if photos_ else float("nan"),
        "zero_action": sum(p["zero_action"] for p in photos_),
        "areas": len(areas), "outside": sum(a["outside"] for a in areas),
        "centre": _med([r.get("centre_error") for r in rows]), "size": _med([r.get("size_error") for r in rows]),
        "angle": _med([r.get("angle_error") for r in rows]),
        "tilted": len(tilted), "tilted_angle": _med([r["angle_error"] for r in tilted]),
        "tilted_max": max((r["angle_error"] for r in tilted), default=float("nan")),
        "collar": sum(a["collar"] for a in areas), "unmarked": sum(a.get("unmarked", False) for a in areas),
        "iou09": sum(r["iou"] >= 0.9 for r in rows) / len(rows) if rows else float("nan"),
        "missed": sum(r["match"] is None for r in rows),
    }


ORDER = ["all", "worn", "folded", "flat-lay", "hanger", "several", "hoodie", "tilted", "low contrast"]


def blocks(rows: list[dict[str, Any]], photos_: list[dict[str, Any]], names: list[str]) -> dict[str, dict[str, Any]]:
    keep = set(names)
    rows = [r for r in rows if r["photo"] in keep]
    photos_ = [p for p in photos_ if p["photo"] in keep]
    target_r = [r for r in rows if not r["low_contrast"]]
    target_p = [p for p in photos_ if not p["low_contrast"]]
    out = {"all": _block(target_r, target_p)}
    for g in ("worn", "folded", "flat-lay", "hanger", "several"):
        out[g] = _block([r for r in target_r if r["layout"] == g], [p for p in target_p if p["layout"] == g])
    out["hoodie"] = _block([r for r in target_r if r["type"] == "hoodie"], [p for p in target_p if p["type"] == "hoodie"])
    tilted_photos = {r["photo"] for r in target_r if r["tilted"]}
    out["tilted"] = _block([r for r in target_r if r["tilted"]], [p for p in target_p if p["photo"] in tilted_photos])
    out["low contrast"] = _block([r for r in rows if r["low_contrast"]], [p for p in photos_ if p["low_contrast"]])
    return out


def _fmt(b: dict[str, Any]) -> str:
    if not b["garments"]:
        return "      -"
    f = lambda v, s: ("   n/a" if v != v else s.format(v))  # noqa: E731
    return (f"{b['photos']:3d} ph {b['garments']:3d} g | IoU>=0.8 {f(b['iou08'], '{:6.1%}')} med {f(b['median_iou'], '{:.2f}')}"
            f" >=0.9 {f(b['iou09'], '{:6.1%}')} | every>=0.8 {f(b['every08'], '{:6.1%}')} zero-action {b['zero_action']:2d}"
            f" | outside {b['outside']:2d}/{b['areas']:<3d} collar {b['collar']:2d} missed {b['missed']:2d}"
            f" | centre {f(b['centre'], '{:.3f}')} size {f(b['size'], '{:.3f}')} angle {f(b['angle'], '{:4.1f}')}"
            f" (tilted {b['tilted']}: med {f(b['tilted_angle'], '{:4.1f}')} max {f(b['tilted_max'], '{:4.1f}')})")


def report_lines(tag: str, rows: list[dict[str, Any]], photos_: list[dict[str, Any]], s: dict[str, Any]) -> list[str]:
    lines = [f"== {tag}  (target set: black-on-black / white-on-white apart; centre and size in marked chest widths)"]
    secs = [p["seconds"] for p in photos_]
    if secs:
        lines.append(f"detection: median {statistics.median(secs):.1f} s, max {max(secs):.1f} s (this machine, dev container)")
    for which in ("dev", "holdout"):
        lines.append(f"-- {which}")
        for name, b in blocks(rows, photos_, s[which]).items():
            lines.append(f"   {name:13} {_fmt(b)}")
    return lines


# --- learning simulation -----------------------------------------------------------------------


def learn(tag: str, names: list[str], key: dict[str, Any], dets: dict[str, Any], jobs: int) -> list[str]:
    """In each layout (and set), the first LEARN_FIRST photos by name are confirmed with the
    key's area; the seller's ratios learnt from them place the rest."""
    from app.pipeline import print_rule

    if not hasattr(print_rule, "observe"):
        return ["(no learning in this version)"]
    by_layout: dict[str, list[str]] = {}
    for n in sorted(names):
        by_layout.setdefault(GROUPS.get(key["photos"][n]["layout"], "other"), []).append(n)
    tables, firsts = {}, set()
    for layout, items in by_layout.items():
        first, rest = items[:LEARN_FIRST], items[LEARN_FIRST:]
        firsts.update(first)
        obs = []
        for n in first:
            k = key["photos"][n]
            if len(dets[n]["garments"]) > print_rule.LEARN_MAX_GARMENTS:
                continue  # confirmed, but not learnt from (as the app does)
            rows, _ = score_photo(n, k, dets[n])
            for r in rows:
                if r["match"] is None or r["iou"] <= 0:
                    continue
                g = dets[n]["garments"][r["match"]]
                w, h = k["width"], k["height"]
                px = lambda p: (p[0] * w, p[1] * h)  # noqa: E731
                o = print_rule.observe(px(g["neck"]), tuple(px(p) for p in g["armpits"]),
                                       [px(p) for p in k["garments"][r["garment"]]["area"]], kind=g["kind"],
                                       layout=g["layout"], angle=g.get("angle") or 0.0,
                                       suggested=[px(p) for p in g["area"]])
                if o:
                    obs.append(o)
        table = print_rule.personal_table(obs)
        for n in rest:
            tables[n] = table
    rest_names = sorted(tables)
    again = _detect_many(rest_names, jobs, tables)
    rows_b, photos_b, rows_a, photos_a = [], [], [], []
    for n in rest_names:
        r, p = score_photo(n, key["photos"][n], dets[n])
        rows_b += r
        photos_b.append(p)
        r, p = score_photo(n, key["photos"][n], again[n])
        rows_a += r
        photos_a.append(p)
    (OUT / tag / "learn.json").write_text(json.dumps({"confirmed": sorted(firsts), "rows": rows_a, "photos": photos_a}, indent=1))
    lines = [f"== learning: first {LEARN_FIRST} photos of each layout confirmed; the other {len(rest_names)} measured"]
    for label, rr, pp in (("without", rows_b, photos_b), ("with   ", rows_a, photos_a)):
        for name, b in blocks(rr, pp, rest_names).items():
            if name in ("all", "worn", "folded", "flat-lay", "several", "hoodie"):
                lines.append(f"   {label} {name:9} {_fmt(b)}")
    return lines


# --- commands ----------------------------------------------------------------------------------


def bench(tag: str, which: str = "all", do_learn: bool = False, jobs: int = 2, only: list[str] | None = None) -> None:
    s = split()
    key = answer_key()
    names = s["dev"] + s["holdout"] if which == "all" else s[which]
    if only:
        names = [n for n in names if n in only or GROUPS.get(key["photos"][n]["layout"]) in only]
    target = OUT / tag
    target.mkdir(parents=True, exist_ok=True)
    dets = _detect_many(names, jobs)
    rows, photos_ = [], []
    for n in names:
        r, p = score_photo(n, key["photos"][n], dets[n])
        rows += r
        photos_.append(p)
    (target / "detections.json").write_text(json.dumps(dets, indent=1))
    (target / "rows.json").write_text(json.dumps({"rows": rows, "photos": photos_}, indent=1))
    lines = report_lines(tag, rows, photos_, s)
    if do_learn:
        for which_ in (["dev", "holdout"] if which == "all" else [which]):
            lines.append(f"-- {which_}")
            lines += learn(tag, [n for n in s[which_] if n in dets], key, dets, jobs)
    (target / "report.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def rescore(tag: str) -> None:
    """Score a run's saved detections again (after the key's metadata changed)."""
    s, key = split(), answer_key()
    dets = json.loads((OUT / tag / "detections.json").read_text())
    rows, photos_ = [], []
    for n in dets:
        r, p = score_photo(n, key["photos"][n], dets[n])
        rows += r
        photos_.append(p)
    (OUT / tag / "rows.json").write_text(json.dumps({"rows": rows, "photos": photos_}, indent=1))
    lines = report_lines(tag, rows, photos_, s)
    (OUT / tag / "report.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def compare(before: str, after: str) -> None:
    s = split()
    lines = []
    for which in ("dev", "holdout"):
        lines.append(f"== {which}: {before} -> {after}")
        b = json.loads((OUT / before / "rows.json").read_text())
        a = json.loads((OUT / after / "rows.json").read_text())
        names = [n for n in s[which] if any(p["photo"] == n for p in a["photos"]) and any(p["photo"] == n for p in b["photos"])]
        bb, aa = blocks(b["rows"], b["photos"], names), blocks(a["rows"], a["photos"], names)
        for g in ORDER:
            lines.append(f"   {g:13} before {_fmt(bb[g])}")
            lines.append(f"   {'':13} after  {_fmt(aa[g])}")
    (OUT / f"compare-{before}-{after}.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def sheet(tag: str, names: list[str], longest: int = 640) -> Path:
    """Photos with the key (green), the detection (red) and its landmarks (neck blue,
    armpits orange); one sheet, local only (/out/geometry/<tag>/sheet-*.jpg)."""
    key = answer_key()
    dets = json.loads((OUT / tag / "detections.json").read_text())
    panels = []
    files = _paths()
    for n in names:
        photo = me.decode(files[n])
        im = Image.open(io.BytesIO(photo.jpeg)).convert("RGB")
        im.thumbnail((longest, longest))
        w, h = im.size
        d = ImageDraw.Draw(im)
        for kg in key["photos"][n]["garments"]:
            d.polygon([(x * w, y * h) for x, y in kg["area"]], outline=(0, 200, 0), width=3)
        for g in dets[n]["garments"]:
            d.polygon([(x * w, y * h) for x, y in g["area"]], outline=(255, 30, 30), width=3)
            x, y = g["neck"]
            d.ellipse((x * w - 4, y * h - 4, x * w + 4, y * h + 4), fill=(30, 90, 255))
            for x, y in g["armpits"]:
                d.ellipse((x * w - 4, y * h - 4, x * w + 4, y * h + 4), fill=(255, 150, 0))
            d.text(((g["area"][0][0]) * w, g["area"][0][1] * h - 12), f"{g['kind'][:5]} {g['layout'][:6]} {g['confidence']:.2f}",
                   fill=(255, 255, 0))
        canvas = Image.new("RGB", (w, h + 18), (255, 255, 255))
        canvas.paste(im, (0, 0))
        ImageDraw.Draw(canvas).text((4, h + 3), n, fill=(0, 0, 0))
        panels.append(canvas)
    cw, ch = max(p.width for p in panels), max(p.height for p in panels)
    cols = min(3, len(panels))
    out = Image.new("RGB", (cols * cw, ((len(panels) + cols - 1) // cols) * ch), (220, 220, 220))
    for i, p in enumerate(panels):
        out.paste(p, ((i % cols) * cw, (i // cols) * ch))
    path = OUT / tag / f"sheet-{abs(hash(tuple(names))) % 100000}.jpg"
    out.save(path, quality=85)
    return path


def main(argv: list[str]) -> None:
    cmd = argv[0] if argv else "bench"
    args = argv[1:]
    if cmd == "split":
        print(json.dumps({k: v for k, v in split("--force" in args).items() if k != "dev" and k != "holdout"}, indent=1))
    elif cmd == "key":
        k = answer_key("--force" in args)
        print(f"{len(k['photos'])} photos, {sum(len(p['garments']) for p in k['photos'].values())} garments; frozen {k['frozen']}")
    elif cmd == "bench":
        jobs = 2
        if "--jobs" in args:
            jobs = int(args[args.index("--jobs") + 1])
            del args[args.index("--jobs"): args.index("--jobs") + 2]
        learn_ = "--learn" in args
        args = [a for a in args if a != "--learn"]
        only = None
        if "--only" in args:
            only = args[args.index("--only") + 1].split("|")
            del args[args.index("--only"): args.index("--only") + 2]
        bench(args[0], args[1] if len(args) > 1 else "all", learn_, jobs, only)
    elif cmd == "learn":  # the learning simulation on a run's saved detections
        jobs = 3
        s, key = split(), answer_key()
        dets = json.loads((OUT / args[0] / "detections.json").read_text())
        lines = []
        for which_ in args[1:] or ["dev"]:
            lines.append(f"-- {which_}")
            lines += learn(args[0], [n for n in s[which_] if n in dets], key, dets, jobs)
        (OUT / args[0] / "learn.txt").write_text("\n".join(lines) + "\n")
        print("\n".join(lines))
    elif cmd == "rescore":
        rescore(args[0])
    elif cmd == "sheet":
        print(sheet(args[0], args[1:]))
    elif cmd == "compare":
        compare(args[0], args[1])
    else:
        raise SystemExit(f"unknown command {cmd}")


if __name__ == "__main__":
    main(sys.argv[1:])
