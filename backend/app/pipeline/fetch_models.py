"""Download the image-analysis models into the image at build time (docker/Dockerfile).

Run once while the backend image is built: ``python -m app.pipeline.fetch_models DIR``.
Each file is checked against its SHA-256; a different file stops the build. At
runtime nothing is downloaded: the models are read from ``MOCKUP_MODELS_DIR`` and
no photo leaves the server.

Both models are Google's MediaPipe models, licensed under the Apache License 2.0
(their model cards say so; docs/etsy-compliance.md lists them):

* ``selfie_multiclass_256x256.tflite``: per pixel background, hair, body-skin,
  face-skin, clothes, others (accessories).
* ``pose_landmarks_detector.tflite``, from ``pose_landmarker_full.task``
  (BlazePose GHUM 3D, "full"): 33 body landmarks with visibility.
"""

from __future__ import annotations

import hashlib
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

SEGMENTER = "selfie_multiclass_256x256.tflite"
LANDMARKER = "pose_landmarks_detector.tflite"

#: (url, sha256 of the download, the file inside it if it is a bundle, sha256 of that file)
MODELS: dict[str, tuple[str, str, str | None, str]] = {
    SEGMENTER: (
        "https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_multiclass_256x256/float32/latest/selfie_multiclass_256x256.tflite",
        "c6748b1253a99067ef71f7e26ca71096cd449baefa8f101900ea23016507e0e0",
        None,
        "c6748b1253a99067ef71f7e26ca71096cd449baefa8f101900ea23016507e0e0",
    ),
    LANDMARKER: (
        "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task",
        "4eaa5eb7a98365221087693fcc286334cf0858e2eb6e15b506aa4a7ecdcec4ad",
        "pose_landmarks_detector.tflite",
        "82be6d591b9dad7d29fe21dc9fd892bf8b9602c458fb05209283de8282a0c488",
    ),
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, (url, download_sha, member, file_sha) in MODELS.items():
        target = directory / name
        if target.exists() and _sha(target.read_bytes()) == file_sha:
            continue
        with urllib.request.urlopen(url, timeout=120) as response:  # build time only
            data = response.read()
        if _sha(data) != download_sha:
            raise SystemExit(f"{name}: the download is not the expected file (sha256 differs)")
        if member is not None:
            with zipfile.ZipFile(io.BytesIO(data)) as bundle:
                data = bundle.read(member)
        if _sha(data) != file_sha:
            raise SystemExit(f"{name}: not the expected model (sha256 differs)")
        target.write_bytes(data)
        print(f"{name}: {len(data) // 1024} KB")


if __name__ == "__main__":
    fetch(Path(sys.argv[1] if len(sys.argv) > 1 else "/opt/listyro/models"))
