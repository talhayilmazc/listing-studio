"""``psd-inspect`` reads what a Photoshop mockup holds (feature/psd-templates, step 0).

Synthetic PSDs only (tests/psd_builder.py): no customer file is in the repository. The inspector runs the
parser in a child process with limits (pipeline/psd_sandbox.py); a damaged or foreign file fails only itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app.ops.psd_inspect import anonymous_line, run
from app.pipeline import psd_read
from app.pipeline.psd_sandbox import ParseFailed, inspect_file
from tests.psd_builder import Pixel, Smart, build

SIZE = (400, 400)


def _art() -> Image.Image:
    art = Image.new("RGBA", (300, 200), (200, 30, 30, 255))
    ImageDraw.Draw(art).ellipse((50, 50, 250, 150), fill=(20, 20, 200, 255))
    return art


def _rect(x: float, y: float, w: float, h: float) -> list[tuple[float, float]]:
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


@pytest.fixture
def files(tmp_path: Path) -> dict[str, str]:
    art = _art()
    highlight = Image.new("RGBA", SIZE, (255, 255, 255, 0))
    ImageDraw.Draw(highlight).rectangle((0, 0, 399, 120), fill=(255, 255, 255, 90))
    mask = Image.new("L", SIZE, 0)
    mask.paste(255, (120, 120, 260, 230))
    mesh = [(100 + 50 * i, 100 + 40 * j + (10 if i in (1, 2) and j in (1, 2) else 0)) for j in range(4) for i in range(4)]
    cases = {
        "plain": [Smart(art, _rect(100, 100, 150, 100))],
        "rotated": [Smart(art, [(150, 80), (290, 130), (240, 270), (100, 220)])],
        "perspective": [Smart(art, [(100, 100), (300, 110), (290, 250), (110, 240)])],
        "mesh": [Smart(art, _rect(100, 100, 150, 120), mesh=mesh)],
        "mask": [Smart(art, _rect(100, 100, 200, 140), mask=mask)],
        "blends": [Smart(art, _rect(100, 100, 150, 100), blend="MULTIPLY"),
                   Pixel(highlight, name="Shine", blend="SCREEN", opacity=180)],
        "two": [Smart(art, _rect(40, 40, 100, 70), name="Back print"),
                Smart(art, _rect(150, 150, 200, 140), name="Your design")],
        "filter": [Smart(art, _rect(100, 100, 150, 100), filters=["Displace"])],
        "linked": [Smart(art, _rect(100, 100, 150, 100), linked=True)],
    }
    out = {}
    for name, layers in cases.items():
        path = str(tmp_path / f"{name}.psd")
        build(path, SIZE, layers)
        out[name] = path
    (tmp_path / "corrupt.psd").write_bytes(Path(out["plain"]).read_bytes()[:3000])
    out["corrupt"] = str(tmp_path / "corrupt.psd")
    (tmp_path / "photo.psd").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 100)
    out["not_psd"] = str(tmp_path / "photo.psd")
    return out


def test_each_shape_and_warp_is_named(files) -> None:
    shapes = {name: inspect_file(files[name]).smart_objects[0] for name in ("plain", "rotated", "perspective", "mesh")}
    assert {n: s.shape for n, s in shapes.items()} == {
        "plain": "axis-aligned", "rotated": "rotated or scaled", "perspective": "perspective", "mesh": "axis-aligned"}
    assert shapes["plain"].warp == "none" and shapes["mesh"].warp == "custom mesh" and shapes["mesh"].warp_points == 16
    assert shapes["plain"].embedded_size == (300, 200) and shapes["plain"].kind == "embedded"
    assert shapes["perspective"].quad[2] == (290.0, 250.0)


def test_masks_blends_filters_and_several_smart_objects(files) -> None:
    assert inspect_file(files["mask"]).smart_objects[0].has_mask
    assert not inspect_file(files["plain"]).smart_objects[0].has_mask  # only when the designer added one
    blends = inspect_file(files["blends"])
    assert [(layer.name, layer.blend_mode, layer.opacity) for layer in blends.layers][1:] == [
        ("Your design", "multiply", 255), ("Shine", "screen", 180)]
    assert inspect_file(files["filter"]).smart_objects[0].smart_filters == ["Displace..."]
    two = inspect_file(files["two"]).smart_objects
    assert [s.name for s in two] == ["Back print", "Your design"] and two[1].area > two[0].area


def test_a_linked_smart_object_is_reported_never_read_from_disk(files) -> None:
    summary = inspect_file(files["linked"])
    assert summary.smart_objects[0].kind == "linked" and summary.smart_objects[0].embedded_size is None
    assert any("linked, not embedded" in line for line in summary.unreadable)


def test_damaged_and_foreign_files_fail_alone(files) -> None:
    for name in ("corrupt", "not_psd"):
        with pytest.raises(ParseFailed):
            inspect_file(files[name])
    text, fine = run([files["corrupt"], files["plain"]])
    assert not fine and "could not be read" in text and "smart objects (1)" in text


def test_a_parse_over_the_memory_limit_fails_only_that_file(files) -> None:
    with pytest.raises(ParseFailed, match="memory"):
        inspect_file(files["plain"], memory_mb=40)


def test_the_summary_line_names_nothing(files) -> None:
    summary = psd_read.read(files["two"])
    line = anonymous_line(summary, 1)
    assert "Back print" not in line and "Your design" not in line and "two.psd" not in line
    assert line.startswith("sample 1: 400x400 PSD RGB 8-bit") and "2 smart object(s)" in line


def test_the_cli_reads_only(files, capsys) -> None:
    from app.cli import main

    before = Path(files["plain"]).read_bytes()
    assert main(["psd-inspect", files["plain"], "--summary"]) == 0
    out = capsys.readouterr().out
    assert "smart objects (1)" in out and "== summary (no names)" in out
    assert Path(files["plain"]).read_bytes() == before
