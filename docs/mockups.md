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

## One product is one area; uploads that say what happened (18 November 2026)

A customer reported two things: a mockup photo with ONE product (one shirt, one print area) was given SEVERAL
areas, and of five photos chosen for one template the fifth never arrived while its row sat at "100%" with no word.

### Measured first

There were no files in `samples/mockups-customer/` (the folder is covered by `.gitignore`: `samples/`), so the
measurement used the evaluation set of the earlier geometry work: 81 of a seller's own blank photos with a frozen
answer key (how many garments each really has, and where the print goes), kept outside the repo and read through a
read-only mount (`evals/geometry_eval.py`). Each photo went through the worker's own analysis
(`mockups.prepare_blank`), with no seller input, on the code before this work and after it.

| | Before | After |
|---|---|---|
| Photos of ONE product (62) given more than one area | **4** | **0** |
| Areas put on those 62 photos in all | 67 | 62 |
| Photos of several products (19) read as several or front & back | 10 | 10 (unchanged) |

The four, and why:

| Photo | Really | Before | Why | After |
|---|---|---|---|---|
| a folded violet shirt, a necklace across it, a hat and a palm leaf beside it | 1 | 3 areas | the necklace cut the shirt's fabric in two regions; the leaf was a third | 1, on the chest |
| a folded blue shirt, a necklace, a hat | 1 | 2 areas | the same cut | 1 |
| a navy tee lying on a flag | 1 | 2 areas ("front & back") | the flag's navy field, the shirt's colour and size, read as the shirt's back | 1 |
| a tee with a cap, shorts and shoes around it | 1 | 2 areas | the cap (0.38 of the tee's size) read as a second garment | 1, on the tee |

On the evaluation's own detector path (`geometry_eval bench`, which analyses a smaller copy) a fifth shape showed:
a cream shirt on white that the analysis did not find at all, with a hat and sandals read as two garments (0.38
and 0.62 confidence).

### The rule now: one area unless the analysis is clearly sure of several

`pipeline/garments.py`:

* **Pieces of one garment are one garment** (`same_garment`, `join_pieces`, `PIECES`). Two regions of the same
  colour (chroma-weighted CIELAB difference under 8) that lie over each other along a long border (the overlap of
  the two, each grown a little, at least 2.5 sides of the smaller) and whose union is one solid shape (0.91 of its
  convex hull) are one garment. A folded one is read again from the two together: its real outline, collar and
  print area. Measured: the pieces of one shirt 1.7-2.2 / 4.1-6.2 / 0.96; shirts of one colour lying side by side
  0.0-0.1 / 0.6-3.0 / 0.75-0.90, never all three.
* **Several needs garments the analysis is sure of** (`guess_layout`, `LAYOUT`): a candidate is counted only with
  confidence 0.45 or more (`sure`); with exactly two candidates the smaller must be at least half the larger
  (`second`; the cap was 0.38, two shirts side by side 0.79-1.0; three or more garments are counted as they are, so
  ten small shirts beside a bigger one are still several); a region other than the largest that runs along two
  edges of the photo is the backdrop, not a garment (`edge`).
* Otherwise the photo has **one** area, on its main garment. Every garment found is still kept as a candidate.

The thresholds were set on all 81 photos, and the four failures happen to be in the evaluation's holdout part: the
table above is a measurement of this set, not a held-out result.

### The seller's final say

* **"This photo has one product"** (the photo's tile and the editor, wherever a photo has more than one area or a
  layout other than one garment; `POST /api/mockup-photos/{id}/one-product`): one click. Every area becomes one:
  the garment the areas lie on, read whole (`garments.whole_at`; no model runs), else the analysis's main garment,
  else the largest of the areas. The layout is "One garment" by the seller's choice; the photo then waits for
  "Looks right".
* Extra areas are deleted, and any area moved, resized and turned by hand in the editor, as before.
* **A later analysis never overwrites the seller's correction**: an area the seller set (`source` "seller",
  "tap", "copied") or confirmed is not replaced by `prepare`, by choosing the layout again (`resuggest`) or by
  `mockup-redetect`; a layout the seller chose is not replaced either. Tested with the real analysis run again.

### Placement and rendering

* The joined shirt gets its area from the whole shirt: on the two folded shirts above the area's overlap with the
  marked one went from 0.28 to 0.42 and from 0.33 to 0.56 (evaluation path); folded photos of the
  holdout part, median overlap 0.45 to 0.57. Placement on the other photos is unchanged
  (the dev part: 0.8 overlap on 17.4% of areas and a median of 0.66 before and after; areas off the garment in the holdout part 4 of 37 before, 2 of 33 after; areas on a prop 3 before, 1 after): this work did not retune the print rule, and the earlier target (0.8 overlap on most photos) is
  still not met.
* **A design is never cut off inside its print area** (`with_unmasked_areas`): every area the seller set is part of
  what can be printed on. Before, the print was clipped to the garment's outline as the analysis drew it, and a
  hole in that outline (a label, a necklace, a pale shirt's edge on a pale backdrop) cut the design there. Outside
  the area the outline still clips (a design the seller enlarged past its area), and "Design goes behind hair and
  arms" is the seller's own switch.
* **The ink sits in the cloth**: the print's edge is softened to the photo's own softness (a blur of 1/2800 of the
  width, inwards only: no ink outside the design), and a little more of the fabric's weave shows in the ink
  (`TEXTURE_AMOUNT` 0.035 to 0.05). The ink's colour is unchanged (the same mean colour inside a flat red, to the
  level). Letting the fabric show through the ink (94% cover) was tried on the samples and dropped: mixed in
  linear light it washed a red out to salmon, the "faded" look fixed earlier.
* Linear-light mixing and the output sharpening are as they were. `ENGINE_VERSION` 5: a mockup made before is made
  again when its run is run again.

Before and after images of each sample (the analysis's own areas, our sample design, no seller input) are written
to `frontend/test-results/mockups-before-after/` (git-ignored; not committed).

### Uploading a template photo

On production four of five requests reached the API (4 x 201); the fifth did not, and the page waited up to three
minutes a try, three tries, showing "100%". `lib/mockupPhotoUpload.ts`, `api/mockups.py`:

* A photo over 8 MB goes up in 8 MB pieces, as a Photoshop mockup does (`PUT /api/mockup-templates/{id}/photos
  ?upload=&offset=&size=`), each piece tried again by itself (4 tries); a piece that arrived twice changes nothing,
  one that would leave a gap is refused with where to go on; then `POST …/photos/assembled` makes the photo. A
  smaller photo is one request, as before.
* The row never stays at 100%: once the bytes are sent it says "Processing…".
* No answer 60 seconds after the last byte, or an answer that is not a success: "Couldn't upload <file>. Try
  again" on that row, with the server's own reason when it gave one. Try again sends that file only, into the same
  template.
* Every file carries the page's own id: a repeat of a request whose answer was lost answers with the photo it
  already became, so trying again never adds a photo twice. Unfinished pieces and these records are removed after
  a day.
* Two photos at a time.

No migration: the pieces of an upload are files in the account's own storage folder.

Tests: `tests/test_mockup_single_product.py`, `lib/mockupPhotoUpload.test.ts`, `e2e/mockupTemplates.spec.ts` (375 and
1366 px: a photo over 8 MB in pieces, a lost request, Try again, "Processing…", one area for one shirt; two shirts,
"This photo has one product", an area moved by hand and still there after the layout is chosen again).
