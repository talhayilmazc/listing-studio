# The paused Prompt 4 branch: what is left (15 November 2026)

Written for the owner, to decide what happens to `wip/prompt4-paused`. Read only: the branch was not merged, rebased or changed.

## What the branch contains

One commit on top of `1f04987` (the season planner, 2 November 2026):

**`43fcca5` "WIP Prompt 4, feature 3 (first run with a sample): paused for a customer request"** (3 November 2026; 25 files, +630 lines; "browser checks and measurement were mid-run when paused").

- `backend/app/pipeline/first_run.py`: a sample design, a blank shirt drawn with Pillow in four colours, mockups made in memory by the mockup engine (`mockups.build_maps`, `render`), and a listing text written in advance. Nothing stored, no model call, no allowance, no Etsy.
- `backend/app/api/first_run.py`: `GET /api/first-run` (the sample's text) and `GET /api/first-run/image` (the pictures, versioned).
- `frontend/app/(app)/first-run/page.tsx`: a five-step walk-through (design, template, mockups, title and tags, a finished draft preview: "A preview only. Nothing has gone to Etsy, and this sample never will."), then Connect a shop or Upload.
- `frontend/lib/firstRunPath.ts` (+ test), `lib/sampleVersion.ts`; an offer in the old `FirstRunGuide` ("Try it with a sample"); `/first-run` in robots and the layout test; `e2e/firstRun.spec.ts` (no request but GETs); `tests/test_first_run.py` (no table changes); screenshots in `docs/screens/first-run/`; a CLAUDE.md paragraph.

**Not on the branch:** the same-text warning. No commit, file or note about it exists on the branch or on master. Features 1 and 2 of Prompt 4 are not on the branch either; nothing in the repository names them.

**Migrations:** none. Nothing to renumber (master is at `0105`).

## What master already does

| Branch idea | On master now |
|---|---|
| A sample a new seller can try before connecting a shop | **Yes, differently**: "Example designs" (app/onboarding-v1, 8 November): three typographic designs with light and dark mockups and listings written once by our AI (`pipeline/examples.py`, `app/examples/`), opened as a normal batch on the real review page, marked "Example", never counted, never sent (every send path refuses with 409) |
| Pre-written text, no model call, nothing counted | Yes (the examples) |
| The sample never reaches a shop | Yes, enforced on every send path and in both publish workers |
| A first-run guide that leads to Connect / Upload | Yes, replaced: the server's setup checklist on Home (`SetupChecklist`, `GET /api/onboarding`), each step ticked from what the seller did, and after connecting, a seller with no uploads lands on Upload |
| Mockups drawn live by the engine for the sample | Partly: the example mockups are pre-rendered files, not drawn on request |
| The five-step guided path and the **finished draft preview** | **No.** Nothing on master shows a new seller what the finished Etsy draft looks like before they send one |
| Same-text warning | **No** (never started) |

## Why the branch cannot be merged as it is

- It edits `components/FirstRunGuide.tsx` and `lib/firstRun.ts`, which master deleted (replaced by `SetupChecklist`).
- Its routes and tests use `/templates` and `/templates/designs`; master moved them to `/mockups/templates` and `/mockups`.
- Its test checks the sample text with `listing_health.score`, which no longer exists (Listing health v2 has no score).
- It calls the mockup engine as it was before engine versions 3 and 4 (render keys, sharpening, linear-light mixing); the calls would need checking against today's `mockups.py`.
- Its first-run idea overlaps the example batch: two different "try it" paths would confuse a new seller.

## What is still worth doing

1. **A finished draft preview for the example listings** (the branch's step 5): from an example on the review page, "See the finished draft" shows title, photos in order, tags, price, attributes and section as the draft would carry them, with the branch's sentence that nothing goes to Etsy. Reuses the example data; no new path, no new endpoint beyond a read. Estimate: about half a day with tests and screenshots.
2. **The same-text warning**: needs a specification first (same title or tags across listings in one shop? the same text sent to several shops?), then it fits the existing check (`content.check_listing`) as a note, never a block. Estimate after the spec: about one day.
3. Not worth doing: the separate `/first-run` page, the Pillow-drawn shirt, and live mockup drawing for a sample. The example batch already covers them with real screens.

## Recommendation

**Drop the branch, and redo the remaining ideas fresh on master** (the finished draft preview on the example listings; the same-text warning once specified). Cherry-picking is not worth it: the only reusable pieces are two sentences ("A preview only. Nothing has gone to Etsy, and this sample never will." and the "written in advance" line) and the idea of step 5; every file it touches has moved or gone. Keep the branch on origin until the owner deletes it, as a record.
