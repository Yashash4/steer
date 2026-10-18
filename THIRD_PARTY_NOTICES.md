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

PyMuPDF is used under the GNU AGPL-3.0. That is why this project is licensed AGPL-3.0-only.

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
