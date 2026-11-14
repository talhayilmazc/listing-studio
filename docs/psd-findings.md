# Photoshop mockups (PSD templates): findings

feature/psd-templates. Sellers upload their own Photoshop mockup; Listyro reads its smart objects and turns it
into an ordinary mockup template. This file records what real sellers' PSDs contain, what we support, what we
approximate, and how close our renders come to Photoshop's.

**No customer file, file name, layer name or image goes in this file or in the repository.** The samples stay in
the git-ignored `samples/psd/` folder (`.gitignore`, `.githooks/pre-commit` and
`backend/tests/test_no_dumps_tracked.py` refuse `*.psd`/`*.psb`) and are deleted from the dev stack when the
work is done.

## Step 0: what the samples contain

**Status (14 November 2026): 8 sellers' samples read; all 8 are supportable as they are.** Every file opened in the
sandbox (13 s for all eight, 28 to 137 MB each); nothing was unreadable.

Run it on the samples (read only; the folder is bind-mounted read-only into a throwaway container, no volume):

```
# PowerShell
docker compose run --rm -v "${PWD}/samples/psd:/samples:ro" --entrypoint sh api -c 'python -m app.cli psd-inspect /samples/* --summary'
# Git Bash (a relative ./samples path is mangled by its path conversion)
docker compose run --rm -v "$(pwd -W)/samples/psd:/samples:ro" --entrypoint sh api -c 'python -m app.cli psd-inspect /samples/* --summary'
```

The full listing names layers and is for reading on this machine only. Paste only the lines under
`== summary (no names)` into the table below.

| sample | summary |
|---|---|
| 1 | 3898x4094 PSD RGB 8-bit, 97.3 MB, 3 layers · 1 smart object: axis-aligned/custom mesh, unchanged · blend modes: normal 3 · adjustments 0, fills 0 · cannot read: 0 |
| 2 | 3500x4000 PSD RGB 8-bit, 77.1 MB, 2 layers · 1 smart object: rotated or scaled/custom mesh, unchanged · blend modes: normal 2 · adjustments 0, fills 0 · cannot read: 0 |
| 3 | 2048x2270 PSD RGB 8-bit, 28.3 MB, 3 layers · 1 smart object: rotated or scaled/custom mesh, unchanged · blend modes: normal 3 · adjustments 0, fills 0 · cannot read: 0 |
| 4 | 4813x3213 PSD RGB 8-bit, 42.4 MB, 2 layers · 1 smart object: axis-aligned/custom mesh, unchanged · blend modes: normal 2 · adjustments 0, fills 0 · cannot read: 0 |
| 5 | 3000x2400 PSD RGB 8-bit, 46.7 MB, 3 layers · 1 smart object: axis-aligned/custom mesh, unchanged · blend modes: normal 3 · adjustments 0, fills 0 · cannot read: 0 |
| 6 | 4000x5227 PSD RGB 8-bit, 116.5 MB, 2 layers · 1 smart object: axis-aligned/custom mesh, unchanged · blend modes: normal 2 · adjustments 0, fills 0 · cannot read: 0 |
| 7 | 4000x5461 PSD RGB 8-bit, 136.5 MB, 3 layers · 1 smart object: axis-aligned/custom mesh, unchanged · blend modes: normal 3 · adjustments 0, fills 0 · cannot read: 0 |
| 8 | 3000x2400 PSD RGB 8-bit, 44.4 MB, 2 layers · 1 smart object: axis-aligned/custom mesh, unchanged · blend modes: normal 2 · adjustments 0, fills 0 · cannot read: 0 |

### What the samples have in common

- **One smart object each, embedded** (a PSB inside the PSD, 846 x 826 to 1505 x 1748 px: the design's canvas),
  scaled onto the page: a plain rectangle in six, a slightly rotated one (about 1.5°) in two. No perspective.
- **The warp is a custom mesh that bends nothing.** All eight store a 4 x 4 custom warp whose points form the
  regular grid over its bounds: Photoshop keeps a custom warp a designer opened and never moved. The reader now
  says "custom mesh, unchanged" for that case (`psd_read.mesh_bend`), and the renderer can treat it as no warp.
- **Normal blending everywhere, full opacity.** No multiply or screen shading layers, no layer masks, no clipping,
  no smart filters, no adjustment or fill layers, no layer effects on the smart object. The print sits flat on the
  photo, as Photoshop shows it.
- **Below the smart object:** one opaque photo layer (the whole canvas).
- **Above it, in four files:**
  - three have a pixel layer that is opaque where it exists and clear elsewhere, overlapping the print: something in front of the shirt (a hand, hair, a strap), covering the design;
  - one has a small text label with a stroke effect in a corner, away from the print.

### What we support, approximate, or cannot do

| Feature | In the samples | Plan |
|---|---|---|
| Embedded smart object, scaled/rotated onto the page | 8 of 8 | Supported: the four corners map the design canvas onto the page |
| Perspective corners | 0 | Supported (same corner mapping); synthetic tests only |
| Custom mesh warp, unchanged | 8 of 8 | Treated as no warp |
| Custom mesh warp, bent (one 4 x 4 patch) | 0 | Supported: bicubic bezier patch; synthetic tests only |
| Split mesh (several patches), standard warp styles (arc, flag…) | 0 | Not yet: flagged to the seller; placed without the warp |
| Normal blend, layers above and below | 8 of 8 | Supported |
| Multiply, screen, overlay, soft light, linear burn | 0 | Supported for layers above; synthetic tests only |
| Layer mask / clipping on the smart object | 0 | Mask supported; synthetic tests only |
| Smart filters (Displace and others) | 0 | Not reproduced: said to the seller in plain words |
| Text layer with a layer effect above the print | 1 | Flattened as Photoshop saved it (see the pixel differences) |
| Linked smart objects, vector content, non-RGB, 16-bit | 0 | Refused with a plain reason |


### What the inspector reports

- canvas size, PSD or PSB, colour mode and bit depth, file size, whether the file carries a saved composite;
- the layer tree with each layer's kind, blend mode, opacity, visibility, masks, clipping and layer effects;
- every smart object:
  - embedded or linked;
  - the embedded file's type and canvas size;
  - its four corners on the page (`nonAffineTransform`, else `Trnf`), named axis-aligned, rotated or scaled, or perspective;
  - its warp: none, one of Photoshop's standard styles, a custom mesh with its point count (16 = one 4 x 4 bezier patch), or a split mesh;
  - layer and vector masks and clipping;
  - smart filters by name;
- adjustment and fill layers;
- what we cannot read: linked smart objects (never read from disk), vector or PDF content, colour modes other than RGB, bit depths other than 8, any layer psd-tools fails on.

### How it reads untrusted files

Parsing runs in a child process (`app.pipeline.psd_sandbox`) with an address-space limit (2 GB by default) and a
CPU-time limit (3 minutes), at low priority. The child runs numpy with one BLAS thread: under an address-space limit
OpenBLAS's worker threads could fail to allocate and the process stalled instead of failing. A damaged file, a
foreign file, or one that needs more memory than the limit fails only itself, with one plain sentence
("it is not a Photoshop file, or it is damaged", "it needs more memory than a template may use").

## Library

psd-tools 1.24.0 (MIT): maintained, reads PSD and PSB, exposes smart-object descriptors (transform, warp, embedded
data) and blend modes, and already vetted for the mockup evals. It moved from the `eval` extra into the main
dependencies. Its `[composite]` extra (aggdraw, for vector shapes) is not installed for the app yet; whether the
converter needs it depends on the samples.
