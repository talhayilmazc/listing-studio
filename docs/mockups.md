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
