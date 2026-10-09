"""Which listing group an uploaded image belongs to.

* An image uploaded **inside a folder** belongs to that folder's group, exactly
  as before (one folder = one listing; the folder name gives the SKU).
* An image uploaded **without a folder** (a flat upload, or loose files next to
  folders) is grouped by the SKU in its **file name**: the name is normalised
  first (case; "-1", "_2", " (3)", " copy", "-front", "-back", "_mockup" and a
  short trailing number after a separator are stripped), then read with the
  usual SKU parser. ``BR5229-1.png``, ``br5229_2.jpg``, ``BR5229 (3).jpg`` and
  ``BR5229 copy.png`` are one group, ``BR5229``.
* A name with no readable SKU (``IMG_4411.jpg``, ``Screenshot …``, a word with
  no digit) goes to the **Unsorted** tray (:data:`UNSORTED`), never into a
  guessed group. Unsorted images are not written or drafted until the seller
  moves them into a group or explicitly ignores them.

The group key of a SKU group is the SKU itself; a folder with exactly that name
is the same group.
"""

from __future__ import annotations

import os
import re

from app.pipeline.sku import SkuParser

#: The group key of the Unsorted tray. Not a folder name anyone uses; a folder
#: that happens to be called this is renamed on upload (:func:`folder_key`).
UNSORTED = "~unsorted"

# Suffixes that copies and alternate shots of one design carry. Stripped from the
# end of the name, repeatedly, before the SKU is read ("BR5229-front copy (2)").
_SUFFIXES = (
    re.compile(r"\s*\(\d+\)$"),  # " (3)"
    re.compile(r"[\s_-]+copy(?:[\s_-]*\d+)?$", re.IGNORECASE),  # " copy", " copy 2"
    re.compile(
        r"[\s_-]+(?:front|back|mockup|mock|main|side|detail|closeup|flat|lay|model|preview|thumb|hero)$",
        re.IGNORECASE,
    ),
    re.compile(r"[\s_-]+\d{1,3}$"),  # "-1", "_2", " 03": a short index after a separator
)
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-_]*")
# File names cameras, phones and screenshot tools give: never a SKU.
_DEVICE = re.compile(
    r"^(?:img|dsc|dscn|dscf|dcim|pxl|mvimg|vid|photo|image|pic|screenshot|screen[\s_-]?shot|whatsapp[\s_-]?image|"
    r"untitled|scan|capture)\b|^(?:img|dsc|pxl|p)[\s_-]?\d",
    re.IGNORECASE,
)


def normalised_stem(filename: str) -> str:
    """The name with its extension and copy/view/index suffixes removed."""
    stem = os.path.splitext(os.path.basename(filename.replace("\\", "/")))[0].strip()
    changed = True
    while changed and stem:
        changed = False
        for pattern in _SUFFIXES:
            shorter = pattern.sub("", stem).strip()
            if shorter != stem and shorter:
                stem, changed = shorter, True
    return stem


def sku_of(filename: str, parser: SkuParser | None = None) -> str | None:
    """The SKU a loose file is grouped by, or None (it goes to Unsorted)."""
    stem = normalised_stem(filename)
    if not stem or _DEVICE.search(stem):
        return None
    sku = (parser or SkuParser()).parse(stem)
    if sku:
        # The parser can find a SKU-shaped run inside a longer word ("SUNSET123" ->
        # "NSET123"); a SKU starts at a word boundary, else the whole name is it.
        at = stem.upper().find(sku.upper())
        if at > 0 and stem[at - 1].isalnum():
            sku = stem if _TOKEN.fullmatch(stem) else None
    # A SKU has letters and digits: "mockup" or "front" alone is not one.
    return sku.upper() if sku and _is_sku(sku) else None


def _is_sku(text: str) -> bool:
    return bool(re.search(r"\d", text) and re.search(r"[A-Za-z]", text))


def folder_key(group_key: str | None) -> str | None:
    """A folder's group key as stored: the reserved Unsorted key is never a folder's."""
    if group_key == UNSORTED:
        return f"{UNSORTED} (folder)"
    return group_key or None


#: How the seller asked for the photos to become listings (the upload page's choice):
#:   "folder": one listing per folder; files inside a folder are never split,
#:             whatever their names; loose files are one listing (as before)
#:   "sku":    every photo by the SKU in its file name; a photo whose name has
#:             none takes its folder's SKU, else it waits in Unsorted
#:   "one":    all the photos are one listing
#: None (no choice sent): folders as folders, loose files by SKU.
MODES = ("folder", "sku", "one")
#: The group key of "all these photos are one listing" (and of loose files in
#: "folder" mode): the batch's root group, as before.
ROOT = ""


def place(
    group_key: str | None, filename: str, parser: SkuParser, mode: str | None = None
) -> tuple[str, str | None]:
    """(group key, SKU) for one uploaded file. ``group_key`` is the folder it came in."""
    folder = folder_key(group_key)
    folder_sku = parser.parse_group(folder) if folder else None
    if mode == "folder":
        return (folder, folder_sku or parser.parse(filename)) if folder else (ROOT, parser.parse(filename))
    if mode == "one":
        return ROOT, sku_of(filename, parser) or folder_sku
    if mode == "sku":
        # A folder's name counts only when it is a SKU ("BR6001"), not "Shots".
        named = folder_sku if folder_sku and _is_sku(folder_sku) else None
        sku = sku_of(filename, parser) or named
        return (sku, sku) if sku else (UNSORTED, None)
    if folder:
        return folder, folder_sku or parser.parse(filename)
    sku = sku_of(filename, parser)
    return (sku, sku) if sku else (UNSORTED, None)


def check_mode(mode: str | None) -> str | None:
    if mode is not None and mode not in MODES:
        raise ValueError(f"grouping must be one of {', '.join(MODES)}")
    return mode


def is_unsorted(group_key: str | None) -> bool:
    return group_key == UNSORTED


def summarise(rows: list[tuple[str | None, str | None]]) -> dict[str, int]:
    """How a batch's images were grouped, from (group key, SKU) per image.

    A group whose key is its SKU is counted "by SKU" (a top-level folder named
    exactly the SKU is the same group); any other key is a folder."""
    keys: dict[str, set[str | None]] = {}
    unsorted = 0
    for key, sku in rows:
        if key == UNSORTED:
            unsorted += 1
            continue
        keys.setdefault(key or "", set()).add(sku)
    by_sku = sum(1 for k, skus in keys.items() if k and "/" not in k and skus == {k})
    return {"groups": len(keys), "sku_groups": by_sku, "folder_groups": len(keys) - by_sku, "unsorted": unsorted}


def found_message(summary: dict[str, int], mode: str | None = None) -> str:
    """"Found 42 groups by SKU, 3 photos unsorted" (and the folders, if any)."""
    n = summary["groups"]
    if mode in ("folder", "one"):
        head = f"{n} listing{'s' if n != 1 else ''}" + (" (one per folder)" if mode == "folder" else "")
        if mode == "one" and n == 1:
            head = "All the photos are one listing"
        u = summary["unsorted"]
        return head + (f", {u} photo{'s' if u != 1 else ''} unsorted" if u else "")
    parts = []
    if summary["sku_groups"]:
        parts.append(f"{summary['sku_groups']} group{'s' if summary['sku_groups'] != 1 else ''} by SKU")
    if summary["folder_groups"]:
        parts.append(f"{summary['folder_groups']} from folders")
    head = "Found " + (", ".join(parts) if parts else "no groups")
    n = summary["unsorted"]
    tail = f", {n} photo{'s' if n != 1 else ''} unsorted" if n else ""
    return head + tail
