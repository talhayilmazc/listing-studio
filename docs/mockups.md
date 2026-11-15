# The mockup editor: what it does

Written for the mockup editor work (feature/mockups-editor, 14 November 2026). Each part below says what was
already there before this work, what was missing, and what was added. Code: `backend/app/pipeline/mockups.py`
(the engine), `pipeline/design_edits.py` (the seller's placement and look, with its TypeScript copy
`frontend/lib/designEdits.ts`), `pipeline/psd_template.py` (Photoshop mockups), `api/mockup_edits.py`,
`workers/mockups.py`, `components/DesignEditor.tsx`, `components/MockupUpload.tsx`.

## Before this work

- **Templates** of the seller's own blank photos, each photo confirmed once ("Looks right"), with print areas
  per garment, subgroups (light/dark), colour groups, and Photoshop mockups (PSD) as templates.
- **A run**: every chosen design on every confirmed photo of the chosen templates, in the worker, progress per
  design, results as tiles; "Use for listings"; a ZIP.
- **The design editor** (one design on one photo): move (drag, arrow keys), resize from a corner with "Keep
  proportions", stretch one side, turn (handle), free corners (perspective), opacity, brightness, contrast,
  recolouring parts of the design (rectangle, lasso, wand), "Reset", Save.
- **Live preview**: the editor draws the design in the browser with the engine's own arithmetic (the same corner
  points, look and recolouring; a shared fixture proves it), on the screen-size photo (`medium`, 1200 px).
  Saving re-renders only the touched mockups in the worker, live per tile ("Updating…", then the new file).
- **"Apply this change to…"**: this design on all photos (every template of the run), all designs on this photo,
  all designs on all photos of this template; as a change on top of each one's own adjustment; one Undo.
- **Quality**: colour kept (ICC to sRGB), fabric light and shadow, folds, underbase on dark garments, light and dark
  design variants, soft edges kept when trimming, 4:4:4 JPEG at quality 92.

## What was missing, and what each part adds

| Part | Missing before | Added |
|---|---|---|
| B1 Placement | A Photoshop mockup ignored the seller's placement: its design was always "contain, centred" in the smart object. No "Centre" button. | The placement (move, size, turn, stretch, free corners, look) applies inside the smart object's canvas, as if the layer were resized inside the smart object in Photoshop; the editor previews it from the centred fit. "Centre" on every template. |
| B2 Live preview | (Already there for photos.) PSD photos previewed from the wrong starting fit. | PSD photos preview from their own fit. |
| B3 Apply to all | No "every selected one"; the scopes were named only in the editor. | "Selected mockups" as a scope, alongside the three; Undo of the last change as before. |
| B4 Presets | None. | Placement presets per template ("Chest print", "Full front", "Left pocket"…), saved by the seller, applied with one click. |
| B5 Batch run | A run made everything again; nothing said what had changed. | "Re-run only the changed ones": the mockups whose design file, photo, placement or recolouring changed since they were made. |
| B6 Quality | No sharpening choice; blending in sRGB. | Output sharpening (off, low, medium); gamma-correct blending where the design is mixed with the fabric. |
| B7 Speed | Every render was made again, even when nothing had changed. | Unchanged renders are reused (a render key of everything that decides the pixels). Previews stay at the screen size (1200 px). Times measured below. |

The sections below give each part's details, numbers and screenshots as it is built.

## Details and numbers

- **B1 Placement on PSD templates.** `psd_template.fit_canvas(design, canvas, adjust)`: the design's four corners
  come from the same arithmetic the editor draws with (`design_edits.unit_points`, centred, size 1), it is drawn into
  the smart object's canvas with premultiplied alpha (no fringe at soft edges), and the canvas goes onto the page
  through its corners and mesh as before; what leaves the canvas is cut, as in Photoshop. The look (opacity,
  brightness, contrast) applies too. "Centre" (`design_edits.centred`, TS `centred`) on every template.
- **B2 Live preview.** The editor and the result tiles draw the design from a copy of at most 1,200 px with its
  transparency (`GET /mockup-designs/{id}/file?size=preview`, made once beside the file); the full file is read
  only to render. Saving re-renders only the touched mockups, as before.
- **B3 Apply to all.** Four scopes: this design on all photos (every template of the run), all designs on this
  photo, all designs on all photos of this template, and **the selected mockups** (ticked under the results).
  Each change lands on top of each one's own placement; "Undo the last apply".
- **B4 Presets.** `mockup_placement_preset` (migration 0104): per template, up to 30, named; "Add Full front, Chest
  print and Left pocket" makes three to start from (our own values); a preset is the placement only, each mockup
  keeps its own look; one click on the open mockup, or "All designs" on every design of the template (Undo).
- **B5 Re-run only the changed ones.** `POST /mockup-runs/{id}/rerun-changed`: every mockup of the run is asked
  for; the worker makes again only those whose render key changed and says how many were kept.
- **B6 Quality.** Sharpening per run (off, low, medium: libvips unsharp mask on lightness). The print is mixed
  with the photo in linear light (`MIX_GAMMA` 2.2: soft edges and opacity no longer darken), engine version 4. Light
  and dark design variants and the white underbase on dark garments are unchanged.
- **B7 Speed.** `mockup_output.render_key` (migration 0105): a hash of the engine, the photo (files, areas, size,
  folds, what is in front, a PSD's placement), each design file with its trim, recolouring and placement, and the
  sharpening. A new run keeps every earlier mockup with the same key (the file stays; nothing is rendered), and a
  re-render keeps an unchanged one. The maps of a photo are made only when one of its mockups is really made.

**Measured** (20 designs of 3000 x 3600 px x 5 templates of one photo each, the test image on the same machine,
`MOCKUP_SPEED=1 docker compose run --rm tests -s tests/test_mockup_speed.py`):

| | Before (4c98255) | After |
|---|---|---|
| First run, 100 mockups | 20.1 s (201 ms a mockup) | 19.6-20.5 s (linear-light mixing costs about 2%) |
| The same run again | 18.8 s | 1.0-1.1 s (100 kept) |
| One design moved, then "Re-run only the changed ones" | (not possible) | 1.6 s (1 of 100 made again) |
| Process memory peak during the first run | +514 MB | +467-508 MB |

Screenshots (synthetic demo account, its drawn tees and drawn designs; `frontend/scripts/editor-shots.mjs`):
`docs/screens/mockups-editor/` at 1366 and 375 px: Home's shortcuts, the run form with sharpening, the results with
downloads and the options, the editor, its presets, the Downloads tab.
