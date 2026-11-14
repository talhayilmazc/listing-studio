# Photoshop mockups (PSD templates): findings

feature/psd-templates. Sellers upload their own Photoshop mockup; Listyro reads its smart objects and turns it
into an ordinary mockup template. This file records what real sellers' PSDs contain, what we support, what we
approximate, and how close our renders come to Photoshop's.

**No customer file, file name, layer name or image goes in this file or in the repository.** The samples stay in
the git-ignored `samples/psd/` folder (`.gitignore`, `.githooks/pre-commit` and
`backend/tests/test_no_dumps_tracked.py` refuse `*.psd`/`*.psb`) and are deleted from the dev stack when the
work is done.

## Step 0: what the samples contain

**Status (14 November 2026): waiting for samples.** `samples/psd/` was empty, so nothing below is measured yet.
The inspector is built and tested on synthetic files (`backend/tests/test_psd_inspect.py`).

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
| (none yet) | |

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
