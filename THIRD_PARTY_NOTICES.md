# Third-party notices

This project's own code is licensed under AGPL-3.0-only (see `LICENSE`). It depends on the
packages below, which keep their own licences. Versions are those locked in `uv.lock`; the packages
are installed from PyPI and are not copied into this repository.

## Runtime dependencies

| Package | Version | Licence | Project |
|---|---|---|---|
| PyMuPDF (`pymupdf`) | 1.28.2 | GNU AGPL-3.0, or Artifex commercial licence | https://github.com/pymupdf/PyMuPDF |
| httpx | 0.28.1 | BSD-3-Clause | https://github.com/encode/httpx |
| httpcore | 1.0.9 | BSD-3-Clause | https://github.com/encode/httpcore |
| h11 | 0.16.0 | MIT | https://github.com/python-hyper/h11 |
| anyio | 4.15.1 | MIT | https://github.com/agronholm/anyio |
| idna | 3.20 | BSD-3-Clause | https://github.com/kjd/idna |
| certifi | 2026.7.22 | MPL-2.0 | https://github.com/certifi/python-certifi |
| typing-extensions | 4.16.0 | PSF-2.0 | https://github.com/python/typing_extensions |
| jsonschema | 4.26.0 | MIT | https://github.com/python-jsonschema/jsonschema |
| jsonschema-specifications | 2025.9.1 | MIT | https://github.com/python-jsonschema/jsonschema-specifications |
| referencing | 0.37.0 | MIT | https://github.com/python-jsonschema/referencing |
| rpds-py | 2026.9.1 | MIT | https://github.com/crate-py/rpds |
| attrs | 26.1.0 | MIT | https://github.com/python-attrs/attrs |
| playwright | 1.64.0 | Apache-2.0 | https://github.com/microsoft/playwright-python |
| greenlet | 3.5.6 | MIT AND PSF-2.0 | https://github.com/python-greenlet/greenlet |
| pyee | 14.0.0 | MIT | https://github.com/jfhbrook/pyee |
| edge-tts | 7.3.1 | LGPL-3.0 | https://github.com/rany2/edge-tts |
| imageio-ffmpeg | 0.6.0 | BSD-2-Clause | https://github.com/imageio/imageio-ffmpeg |
| aiohttp | 3.14.5 | Apache-2.0 AND MIT | https://github.com/aio-libs/aiohttp |
| aiohappyeyeballs | 2.7.1 | PSF-2.0 | https://github.com/aio-libs/aiohappyeyeballs |
| aiosignal | 1.4.0 | Apache-2.0 | https://github.com/aio-libs/aiosignal |
| frozenlist | 1.8.0 | Apache-2.0 | https://github.com/aio-libs/frozenlist |
| multidict | 7.1.0 | Apache-2.0 | https://github.com/aio-libs/multidict |
| propcache | 0.5.4 | Apache-2.0 | https://github.com/aio-libs/propcache |
| yarl | 1.25.1 | Apache-2.0 | https://github.com/aio-libs/yarl |
| tabulate | 0.10.0 | MIT | https://github.com/astanin/python-tabulate |

PyMuPDF is used under the GNU AGPL-3.0. That is why this project is licensed AGPL-3.0-only.

Rendering uses Chromium's headless shell, downloaded by Playwright into `code/.browsers/` (not committed;
Chromium is under BSD-3-Clause and the licences listed in its own bundle).

The reel (S7-min) also uses tools that are downloaded, never committed:
- the ffmpeg binary inside imageio-ffmpeg (ffmpeg 7.0.2 for linux-aarch64, built with libx264, so that binary is
  under the GPL);
- whisper.cpp v1.9.1 (MIT), built from source into `code/.tools/` by `tools/setup-whisper.sh`, and its model
  `ggml-base.en.bin` (MIT, from the whisper.cpp model repository) in `code/.models/`, checked by SHA-256.
The reel's voice comes from edge-tts, which calls Microsoft's online text-to-speech service: the text of every reel
line is sent to it.

## Fonts (bundled in `fonts/`)

| Font | Licence | Project | Licence file |
|---|---|---|---|
| Fraunces (variable, upright and italic) | SIL Open Font License 1.1 | https://github.com/undercasetype/Fraunces | `fonts/OFL-Fraunces.txt` |
| Inter (variable) | SIL Open Font License 1.1 | https://github.com/rsms/inter | `fonts/OFL-Inter.txt` |

Both were taken unmodified from the google/fonts repository (`ofl/fraunces`, `ofl/inter`) and renamed only.

## Generated clips (optional: b-roll and the AI presenter placeholder)

`broll/ltx_comfy.py` asks a local ComfyUI to run Lightricks' LTX-2.5 (22B distilled, int8; Gemma-4 12B text encoder;
video/audio VAEs; x2 spatial upscaler), from huggingface.co/Lightricks/LTX-2.5. The weights are not part of this repository.
They are licensed under the **LTX-2.x Community License Agreement** (https://github.com/Lightricks/LTX-2/blob/main/LICENSE-2_x),
which has its own conditions for commercial use; Steer would need to check them before production use. Clips made with it
are labelled "AI presenter · placeholder" (presenter) and pass a frame-sheet review before use.

## Development and test dependencies (not needed to run the pipeline)

| Package | Version | Licence |
|---|---|---|
| pytest | 9.1.1 | MIT |
| pluggy | 1.7.0 | MIT |
| iniconfig | 2.3.1 | MIT |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| Pygments | 2.21.0 | BSD-2-Clause |

## Test documents

The research papers in `tests/fixtures/` are published under CC BY 4.0. Their credits are listed in
`tests/fixtures/ATTRIBUTION.md`.

## Vendored files

| File | Version | Licence | Project |
|---|---|---|---|
| `vendor/p5brush/brush.js` (p5.brush standalone build, copied unchanged) | 2.2.2 (tag `v2.2.2`) | MIT | https://github.com/acamposuribe/p5.brush |
