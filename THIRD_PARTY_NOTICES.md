# Third-Party Notices

This project depends on open-source software listed below. Generated
with `pip-licenses --format=markdown --with-urls` (Python,
resolved from `requirements.txt` plus the `voice`/`dev`/`desktop`
extras in `setup.py`, transitive dependencies included) and the npm
registry's `latest` metadata for each package named in
`dashboard/package.json` (direct dependencies and devDependencies;
transitive npm dependencies are not enumerated; see the note at the
bottom).

License strings are as reported by each package's own metadata and may
use different SPDX conventions (e.g. "MIT License" vs "MIT"); they are
reproduced verbatim rather than normalized.

---

## Upstream template

The dashboard did not start from an empty Next.js app. Its layout, its
Tailwind design tokens and a number of its UI components are derived from
an open-source admin template, and that is the single largest borrowed
work in this repository, larger than any one package in the tables
below.

| Name | License | Source | What we use |
|---|---|---|---|
| admin-one-react-tailwind | MIT | https://github.com/justboil/admin-one-react-tailwind | The dashboard's layout shell, Tailwind theme and several base UI components under `dashboard/app/_components/`, modified. |

The template's own MIT license text ships verbatim at `dashboard/LICENSE`,
which is what MIT §1 requires. This entry is here because a credits file
that omits the biggest borrow is not a credits file.

---

## Hermes Agent (copied patterns and code)

BuildBot copies patterns and short pieces of code from Hermes Agent by Nous
Research (https://github.com/NousResearch/hermes-agent, read at commit
`ea81748579ee1732d214ccb75f91d22208ed623d`). Files that copy from it say so
in their first lines.

- The tool loop in chat copies the shape of Hermes Agent's agent loop and
  tool registry, and its ask-before-each-command approval:
  `src/puffpai/brain_tools.py`, `src/puffpai/brain_loop.py`,
  `src/puffpai/brain_commands.py`.
- `src/puffpai/mcp_client.py` and `src/puffpai/skill_learn.py` copy the MCP
  tool naming `mcp__<server>__<tool>` cut to 64 characters with a hash
  suffix and the input-schema repair (`tools/mcp_tool_schema.py`), the
  optional `mcp` package import (`tools/mcp_tool.py`), and the "learn a
  skill from a docs page" prompt rules (`agent/learn_prompt.py`).

- Parts of `src/puffpai/skills.py` (the SKILL.md frontmatter reading and
  the `<available_skills>` prompt wording) and of `src/puffpai/finding/`
  (`hermes_index.py`: the Hermes skill list's matching order; `sites.py`
  and `install.py`: how ClawHub and skills.sh skills are found and fetched)
  adapt `agent/prompt_builder.py`, `agent/skill_utils.py`,
  `tools/skills_hub_official.py`, `tools/skills_hub_clawhub.py` and
  `tools/skills_hub_skillssh.py`.

Hermes Agent's licence:

```
MIT License

Copyright (c) 2025 Nous Research

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

The `mcp` Python package (the Model Context Protocol SDK,
https://github.com/modelcontextprotocol/python-sdk, MIT) is a new base
requirement for tool servers (`requirements.txt`).

---

## Python dependencies (base runtime + voice/dev/desktop extras, transitive)

| Name                                     | Version     | License                                                        | URL                                                                                                                |
|------------------------------------------|-------------|----------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------|
| Flask                                    | 3.1.3       | BSD-3-Clause                                                   | https://github.com/pallets/flask/                                                                                  |
| Jinja2                                   | 3.1.6       | BSD License                                                    | https://github.com/pallets/jinja/                                                                                  |
| MarkupSafe                               | 3.0.3       | BSD-3-Clause                                                   | https://github.com/pallets/markupsafe/                                                                             |
| PyPika                                   | 0.51.1      | Apache Software License                                        | https://github.com/kayak/pypika                                                                                    |
| PyYAML                                   | 6.0.3       | MIT License                                                    | https://pyyaml.org/                                                                                                |
| Pygments                                 | 2.21.0      | BSD-2-Clause                                                   | https://pygments.org                                                                                               |
| Werkzeug                                 | 3.1.8       | BSD-3-Clause                                                   | https://github.com/pallets/werkzeug/                                                                               |
| aiohappyeyeballs                         | 2.7.1       | Python Software Foundation License                             | https://github.com/aio-libs/aiohappyeyeballs                                                                       |
| aiohttp                                  | 3.14.3      | Apache-2.0 AND MIT                                             | https://github.com/aio-libs/aiohttp                                                                                |
| aiosignal                                | 1.4.0       | Apache Software License                                        | https://github.com/aio-libs/aiosignal                                                                              |
| altgraph                                 | 0.17.5      | MIT License                                                    | https://altgraph.readthedocs.io                                                                                    |
| annotated-doc                            | 0.0.5       | MIT                                                            | https://github.com/fastapi/annotated-doc                                                                           |
| annotated-types                          | 0.8.0       | MIT                                                            | https://github.com/annotated-types/annotated-types                                                                 |
| anyio                                    | 4.15.0      | MIT                                                            | https://anyio.readthedocs.io/en/stable/versionhistory.html                                                         |
| ast_serialize                            | 0.9.0       | MIT                                                            | https://github.com/mypyc/ast_serialize                                                                             |
| attrs                                    | 26.1.0      | MIT                                                            | https://www.attrs.org/en/stable/changelog.html                                                                     |
| bcrypt                                   | 5.0.0       | Apache Software License                                        | https://github.com/pyca/bcrypt/                                                                                    |
| black                                    | 26.5.1      | MIT                                                            | https://github.com/psf/black                                                                                       |
| blinker                                  | 1.9.0       | MIT License                                                    | https://github.com/pallets-eco/blinker/                                                                            |
| build                                    | 1.6.0       | MIT                                                            | https://build.pypa.io                                                                                              |
| certifi                                  | 2026.7.22   | Mozilla Public License 2.0 (MPL 2.0)                           | https://github.com/certifi/python-certifi                                                                          |
| cffi                                     | 2.1.1       | MIT-0                                                          | https://cffi.readthedocs.io/en/latest/whatsnew.html                                                                |
| charset-normalizer                       | 3.5.1       | MIT                                                            | https://github.com/jawah/charset_normalizer/blob/master/CHANGELOG.md                                               |
| chromadb                                 | 1.5.9       | Apache Software License                                        | https://github.com/chroma-core/chroma                                                                              |
| click                                    | 8.5.0       | BSD-3-Clause                                                   | https://github.com/pallets/click/                                                                                  |
| colorama                                 | 0.4.6       | BSD License                                                    | https://github.com/tartley/colorama                                                                                |
| cryptography                             | 50.0.1      | Apache-2.0 OR BSD-3-Clause                                     | https://github.com/pyca/cryptography                                                                               |
| durationpy                               | 0.11        | MIT                                                            | https://github.com/icholy/durationpy                                                                               |
| filelock                                 | 3.32.5      | MIT                                                            | https://github.com/tox-dev/py-filelock                                                                             |
| flake8                                   | 7.3.0       | MIT License                                                    | https://github.com/pycqa/flake8                                                                                    |
| flatbuffers                              | 25.12.19    | Apache Software License                                        | https://google.github.io/flatbuffers/                                                                              |
| frozenlist                               | 1.8.0       | Apache-2.0                                                     | https://github.com/aio-libs/frozenlist                                                                             |
| fsspec                                   | 2026.7.0    | BSD-3-Clause                                                   | https://github.com/fsspec/filesystem_spec                                                                          |
| googleapis-common-protos                 | 1.75.2      | Apache Software License                                        | https://github.com/googleapis/google-cloud-python/tree/main/packages/googleapis-common-protos                      |
| grpcio                                   | 1.83.1      | Apache-2.0                                                     | https://grpc.io                                                                                                    |
| h11                                      | 0.16.0      | MIT License                                                    | https://github.com/python-hyper/h11                                                                                |
| hf-xet                                   | 1.6.0       | Apache-2.0                                                     | https://github.com/huggingface/xet-core                                                                            |
| httpcore                                 | 1.0.9       | BSD-3-Clause                                                   | https://www.encode.io/httpcore/                                                                                    |
| httptools                                | 0.8.0       | MIT                                                            | https://github.com/MagicStack/httptools                                                                            |
| httpx                                    | 0.28.1      | BSD License                                                    | https://github.com/encode/httpx                                                                                    |
| huggingface_hub                          | 1.29.0      | Apache Software License                                        | https://github.com/huggingface/huggingface_hub                                                                     |
| idna                                     | 3.19        | BSD-3-Clause                                                   | https://github.com/kjd/idna                                                                                        |
| importlib_resources                      | 7.1.0       | Apache-2.0                                                     | https://github.com/python/importlib_resources                                                                      |
| iniconfig                                | 2.3.0       | MIT                                                            | https://github.com/pytest-dev/iniconfig                                                                            |
| itsdangerous                             | 2.2.0       | BSD License                                                    | https://github.com/pallets/itsdangerous/                                                                           |
| jaraco.classes                           | 3.4.0       | MIT License                                                    | https://github.com/jaraco/jaraco.classes                                                                           |
| jaraco.context                           | 6.1.2       | MIT                                                            | https://github.com/jaraco/jaraco.context                                                                           |
| jaraco.functools                         | 4.6.0       | MIT                                                            | https://github.com/jaraco/jaraco.functools                                                                         |
| jsonschema                               | 4.26.0      | MIT                                                            | https://github.com/python-jsonschema/jsonschema                                                                    |
| jsonschema-specifications                | 2025.9.1    | MIT                                                            | https://github.com/python-jsonschema/jsonschema-specifications                                                     |
| keyring                                  | 25.7.0      | MIT                                                            | https://github.com/jaraco/keyring                                                                                  |
| kubernetes                               | 36.0.3      | Apache Software License                                        | https://github.com/kubernetes-client/python                                                                        |
| librt                                    | 0.15.0      | MIT                                                            | https://github.com/mypyc/librt                                                                                     |
| markdown-it-py                           | 4.2.0       | MIT License                                                    | https://github.com/executablebooks/markdown-it-py                                                                  |
| mccabe                                   | 0.7.0       | MIT License                                                    | https://github.com/pycqa/mccabe                                                                                    |
| mdurl                                    | 0.1.2       | MIT License                                                    | https://github.com/executablebooks/mdurl                                                                           |
| mmh3                                     | 5.3.0       | MIT License                                                    | https://pypi.org/project/mmh3/                                                                                     |
| more-itertools                           | 11.1.0      | MIT                                                            | https://github.com/more-itertools/more-itertools                                                                   |
| multidict                                | 6.7.1       | Apache License 2.0                                             | https://github.com/aio-libs/multidict                                                                              |
| mypy                                     | 2.3.1       | MIT                                                            | https://www.mypy-lang.org/                                                                                         |
| mypy_extensions                          | 1.1.0       | MIT                                                            | https://github.com/python/mypy_extensions                                                                          |
| numpy                                    | 2.5.2       | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0             | https://numpy.org                                                                                                  |
| oauthlib                                 | 3.3.1       | BSD-3-Clause                                                   | https://github.com/oauthlib/oauthlib                                                                               |
| onnxruntime                              | 1.29.0      | MIT License                                                    | https://onnxruntime.ai                                                                                             |
| opentelemetry-api                        | 1.44.0      | Apache-2.0                                                     | https://github.com/open-telemetry/opentelemetry-python/tree/main/opentelemetry-api                                 |
| opentelemetry-exporter-otlp-proto-common | 1.44.0      | Apache-2.0                                                     | https://github.com/open-telemetry/opentelemetry-python/tree/main/exporter/opentelemetry-exporter-otlp-proto-common |
| opentelemetry-exporter-otlp-proto-grpc   | 1.44.0      | Apache-2.0                                                     | https://github.com/open-telemetry/opentelemetry-python/tree/main/exporter/opentelemetry-exporter-otlp-proto-grpc   |
| opentelemetry-proto                      | 1.44.0      | Apache-2.0                                                     | https://github.com/open-telemetry/opentelemetry-python/tree/main/opentelemetry-proto                               |
| opentelemetry-sdk                        | 1.44.0      | Apache-2.0                                                     | https://github.com/open-telemetry/opentelemetry-python/tree/main/opentelemetry-sdk                                 |
| opentelemetry-semantic-conventions       | 0.65b0      | Apache-2.0                                                     | https://github.com/open-telemetry/opentelemetry-python/tree/main/opentelemetry-semantic-conventions                |
| orjson                                   | 3.12.0      | MPL-2.0 AND (Apache-2.0 OR MIT)                                | https://github.com/ijl/orjson                                                                                      |
| overrides                                | 7.7.0       | Apache License, Version 2.0                                    | https://github.com/mkorpela/overrides                                                                              |
| packaging                                | 26.3        | Apache-2.0 OR BSD-2-Clause                                     | https://github.com/pypa/packaging                                                                                  |
| pathspec                                 | 1.1.1       | Mozilla Public License 2.0 (MPL 2.0)                           | https://python-path-specification.readthedocs.io/en/latest/index.html                                              |
| pathvalidate                             | 3.3.1       | MIT License                                                    | https://github.com/thombashi/pathvalidate                                                                          |
| pdfminer.six                             | 20260107    | MIT                                                            | https://github.com/pdfminer/pdfminer.six                                                                           |
| pdfplumber                               | 0.11.10     | MIT License                                                    | https://github.com/jsvine/pdfplumber                                                                               |
| pefile                                   | 2024.8.26   | MIT                                                            | https://github.com/erocarrera/pefile                                                                               |
| pillow                                   | 12.3.0      | MIT-CMU                                                        | https://python-pillow.github.io                                                                                    |
| piper-tts                                | 1.7.0       | GPL-3.0-or-later                                               | http://github.com/OHF-voice/piper1-gpl                                                                             |
| platformdirs                             | 4.11.7      | MIT                                                            | https://github.com/tox-dev/platformdirs                                                                            |
| pluggy                                   | 1.6.0       | MIT License                                                    | UNKNOWN                                                                                                            |
| propcache                                | 0.5.2       | Apache Software License                                        | https://github.com/aio-libs/propcache                                                                              |
| protobuf                                 | 7.36.1      | 3-Clause BSD License                                           | https://developers.google.com/protocol-buffers/                                                                    |
| pybase64                                 | 1.5.0       | BSD-2-Clause                                                   | https://github.com/mayeut/pybase64                                                                                 |
| pycodestyle                              | 2.14.0      | MIT                                                            | https://pycodestyle.pycqa.org/                                                                                     |
| pycparser                                | 3.0         | BSD-3-Clause                                                   | https://github.com/eliben/pycparser                                                                                |
| pydantic                                 | 2.13.5      | MIT                                                            | https://github.com/pydantic/pydantic                                                                               |
| pydantic-settings                        | 2.15.0      | MIT                                                            | https://github.com/pydantic/pydantic-settings                                                                      |
| pydantic_core                            | 2.46.5      | MIT                                                            | https://github.com/pydantic                                                                                        |
| pyflakes                                 | 3.4.0       | MIT License                                                    | https://github.com/PyCQA/pyflakes                                                                                  |
| pyinstaller                              | 6.22.2      | GNU General Public License v2 (GPLv2)                          | https://pyinstaller.org                                                                                            |
| pyinstaller-hooks-contrib                | 2026.7      | Apache Software License; GNU General Public License v2 (GPLv2) | https://github.com/pyinstaller/pyinstaller-hooks-contrib                                                           |
| pypdf                                    | 6.16.2      | BSD-3-Clause                                                   | https://github.com/py-pdf/pypdf                                                                                    |
| pypdfium2                                | 5.13.0      | BSD-3-Clause, Apache-2.0, dependency licenses                  | https://github.com/pypdfium2-team/pypdfium2                                                                        |
| pyproject_hooks                          | 1.2.0       | MIT License                                                    | https://github.com/pypa/pyproject-hooks                                                                            |
| pytest                                   | 9.1.1       | MIT                                                            | https://docs.pytest.org/en/latest/                                                                                 |
| python-dateutil                          | 2.9.0.post0 | Apache Software License; BSD License                           | https://github.com/dateutil/dateutil                                                                               |
| python-dotenv                            | 1.2.3       | BSD-3-Clause                                                   | https://github.com/theskumar/python-dotenv                                                                         |
| pytokens                                 | 0.4.1       | MIT License                                                    | https://github.com/tusharsadhwani/pytokens                                                                         |
| pywin32-ctypes                           | 0.2.3       | BSD-3-Clause                                                   | https://github.com/enthought/pywin32-ctypes                                                                        |
| referencing                              | 0.37.0      | MIT                                                            | https://github.com/python-jsonschema/referencing                                                                   |
| requests                                 | 2.34.2      | Apache Software License                                        | https://github.com/psf/requests                                                                                    |
| requests-oauthlib                        | 2.0.0       | BSD License                                                    | https://github.com/requests/requests-oauthlib                                                                      |
| rich                                     | 15.0.0      | MIT License                                                    | https://github.com/Textualize/rich                                                                                 |
| rpds-py                                  | 2026.6.3    | MIT                                                            | https://github.com/crate-py/rpds                                                                                   |
| shellingham                              | 1.5.4       | ISC License (ISCL)                                             | https://github.com/sarugaku/shellingham                                                                            |
| six                                      | 1.17.0      | MIT License                                                    | https://github.com/benjaminp/six                                                                                   |
| sounddevice                              | 0.5.6       | MIT                                                            | https://github.com/spatialaudio/python-sounddevice/                                                                |
| srt                                      | 3.5.3       | MIT License                                                    | https://github.com/cdown/srt                                                                                       |
| tenacity                                 | 9.1.4       | Apache Software License                                        | https://github.com/jd/tenacity                                                                                     |
| tokenizers                               | 0.23.1      | Apache Software License                                        | https://github.com/huggingface/tokenizers                                                                          |
| tqdm                                     | 4.70.0      | MPL-2.0 AND MIT                                                | https://tqdm.github.io                                                                                             |
| typer                                    | 0.27.2      | MIT                                                            | https://github.com/fastapi/typer                                                                                   |
| typing-inspection                        | 0.4.4       | MIT                                                            | https://github.com/pydantic/typing-inspection                                                                      |
| typing_extensions                        | 4.16.0      | PSF-2.0                                                        | https://github.com/python/typing_extensions                                                                        |
| urllib3                                  | 2.7.0       | MIT                                                            | https://github.com/urllib3/urllib3/blob/main/CHANGES.rst                                                           |
| uvicorn                                  | 0.52.4      | BSD-3-Clause                                                   | https://uvicorn.dev/                                                                                               |
| vosk                                     | 0.3.45      | Apache Software License                                        | https://github.com/alphacep/vosk-api                                                                               |
| watchfiles                               | 1.2.0       | MIT License                                                    | https://github.com/samuelcolvin/watchfiles                                                                         |
| websocket-client                         | 1.9.2       | Apache-2.0                                                     | https://github.com/websocket-client/websocket-client                                                               |
| websockets                               | 17.1        | BSD-3-Clause                                                   | https://github.com/python-websockets/websockets                                                                    |
| yarl                                     | 1.24.5      | Apache-2.0                                                     | https://github.com/aio-libs/yarl                                                                                   |
| zstandard                                | 0.25.0      | BSD-3-Clause                                                   | https://github.com/indygreg/python-zstandard                                                                       |

---

## JavaScript dependencies (dashboard/, direct only)

| Name | Version | License | Homepage |
|---|---|---|---|
| @dnd-kit/core | 6.3.1 | MIT | https://github.com/clauderic/dnd-kit#readme |
| @dnd-kit/sortable | 10.0.0 | MIT | https://github.com/clauderic/dnd-kit#readme |
| @dnd-kit/utilities | 3.2.2 | MIT | https://github.com/clauderic/dnd-kit#readme |
| @mdi/js | 7.4.47 | Apache-2.0 | https://github.com/Templarian/MaterialDesign-JS#readme |
| @react-three/drei | 10.7.8 | MIT | https://github.com/pmndrs/drei |
| @react-three/fiber | 9.7.0 | MIT | https://github.com/pmndrs/react-three-fiber#readme |
| @reduxjs/toolkit | 2.12.0 | MIT | https://redux-toolkit.js.org |
| @types/three | 0.185.4 | MIT | https://github.com/DefinitelyTyped/DefinitelyTyped/tree/master/types/three |
| chart.js | 4.5.1 | MIT | https://www.chartjs.org |
| formik | 2.4.9 | Apache-2.0 | https://formik.org |
| next | 16.3.4 | MIT | https://nextjs.org |
| numeral | 2.0.6 | MIT | http://numeraljs.com |
| react | 19.2.8 | MIT | https://react.dev/ |
| react-chartjs-2 | 5.3.1 | MIT | https://github.com/reactchartjs/react-chartjs-2 |
| react-dom | 19.2.8 | MIT | https://react.dev/ |
| react-redux | 9.3.0 | MIT | https://github.com/reduxjs/react-redux |
| swr | 2.5.1 | MIT | https://swr.vercel.app |
| three | 0.185.1 | MIT | https://threejs.org/ |
| @eslint/eslintrc | 3.3.7 | MIT | https://github.com/eslint/eslintrc#readme |
| @tailwindcss/forms | 0.5.11 | MIT | https://github.com/tailwindlabs/tailwindcss-forms#readme |
| @tailwindcss/postcss | 4.3.3 | MIT | https://tailwindcss.com |
| @types/lodash | 4.17.25 | MIT | https://github.com/DefinitelyTyped/DefinitelyTyped/tree/master/types/lodash |
| @types/node | 26.4.1 | MIT | https://github.com/DefinitelyTyped/DefinitelyTyped/tree/master/types/node |
| @types/numeral | 2.0.5 | MIT | https://github.com/DefinitelyTyped/DefinitelyTyped/tree/master/types/numeral |
| @types/react | 19.2.18 | MIT | https://github.com/DefinitelyTyped/DefinitelyTyped/tree/master/types/react |
| @types/react-dom | 19.2.5 | MIT | https://github.com/DefinitelyTyped/DefinitelyTyped/tree/master/types/react-dom |
| @types/react-redux | 7.1.34 | MIT | https://github.com/DefinitelyTyped/DefinitelyTyped/tree/master/types/react-redux |
| eslint | 10.9.1 | MIT | https://eslint.org |
| eslint-config-next | 16.3.4 | MIT | https://nextjs.org/docs/app/api-reference/config/eslint |
| eslint-config-prettier | 10.1.8 | MIT | https://github.com/prettier/eslint-config-prettier#readme |
| postcss | 8.5.26 | MIT | https://postcss.org/ |
| prettier | 3.9.6 | MIT | https://prettier.io |
| prettier-plugin-tailwindcss | 0.8.1 | MIT | https://github.com/tailwindlabs/prettier-plugin-tailwindcss#readme |
| tailwindcss | 4.3.3 | MIT | https://tailwindcss.com |
| typescript | 7.0.2 | Apache-2.0 | https://www.typescriptlang.org/ |
| talkinghead | github:met4citizen/TalkingHead#c22aed4 (2026-02-26) | MIT (per repo LICENSE) | https://github.com/met4citizen/TalkingHead |

---

## Fonts

No font is ever fetched from a CDN, at runtime or at build time. The three
text families the dashboard design calls for are **vendored into this
repository** under `dashboard/app/_fonts/` and loaded with
`next/font/local` from `dashboard/app/layout.tsx`, so the intended look
survives a build on a box with no egress. All three are
SIL Open Font License 1.1; each family's unmodified upstream `OFL.txt` sits
beside its files, and
[`dashboard/app/_fonts/LICENSE-fonts.txt`](dashboard/app/_fonts/LICENSE-fonts.txt)
indexes them with the copyright line from each font's own name table.

| Family | Files (`dashboard/app/_fonts/…`) | Role / CSS variable | License | Upstream |
|---|---|---|---|---|
| Fredoka | `fredoka/Fredoka[wdth,wght].woff2` (variable, wght 300–700) | Display headings, `--font-fredoka` | OFL-1.1 (`fredoka/OFL.txt`) | https://github.com/google/fonts/tree/a60a77e14f28abd4ef243a1b5dfc48df0cec5205/ofl/fredoka |
| Nunito | `nunito/Nunito[wght].woff2` (variable, wght 200–1000) | Body / UI text, `--font-nunito` | OFL-1.1 (`nunito/OFL.txt`) | https://github.com/google/fonts/tree/8b0a1d0f5983c89bc2b93f1b5fb55f9e252744b5/ofl/nunito |
| JetBrains Mono | `jetbrains-mono/JetBrainsMono-{Regular,Medium,SemiBold,Bold}.woff2` (400/500/600/700) | Stats, code, numerals, `--font-jbmono` | OFL-1.1 (`jetbrains-mono/OFL.txt`) | https://github.com/JetBrains/JetBrainsMono/tree/cd5227bd1f61dff3bbd6c814ceaf7ffd95e947d9/fonts/webfonts (tag `v2.304`) |

The two Google variable fonts were fetched as upstream TTFs and converted to
WOFF2 with `fontTools`: same glyph set, same axes, no subsetting. The
JetBrains Mono WOFF2 files are byte-identical to upstream.

[`dashboard/css/_fonts.css`](dashboard/css/_fonts.css) (ours, not
third-party) composes each of the three theme variables as "vendored file
first, then an operating-system stack", so a role degrades to a system face
rather than to a default serif if a file ever fails to load.

The standalone HTML apps in `dashboard/public/*.html` do not go through the
Next.js bundler and still map their families (Nunito, Exo 2, Quicksand,
Baloo 2, Fira Code) onto operating-system stacks, in
[`dashboard/public/vendor/fonts/app-fonts.css`](dashboard/public/vendor/fonts/app-fonts.css)
(`--app-rounded`, `--app-sans`, `--app-mono`), also ours, and also without
a single request to `fonts.googleapis.com`.

The other font *file* in the repository is the icon face listed under
"Other vendored browser assets" below.

---

## Other vendored browser assets

Files committed into `dashboard/public/` that no package manager can see,
so neither table above lists them:

| Asset | Path | License | Upstream |
|---|---|---|---|
| React (UMD production build) | `dashboard/public/vendor/js/react.production.min.js` | MIT (per the file's own `@license` header) | https://react.dev/ |
| React DOM (UMD production build) | `dashboard/public/vendor/js/react-dom.production.min.js` | MIT (per the file's own `@license` header) | https://react.dev/ |
| Babel standalone | `dashboard/public/vendor/js/babel.min.js` | MIT | https://babeljs.io/docs/babel-standalone |
| TalkingHead English lip-sync | `dashboard/public/talkinghead/lipsync-en.mjs` | MIT (per the TalkingHead repository's LICENSE) | https://github.com/met4citizen/TalkingHead |
| Material Symbols Rounded (icon font) | `dashboard/public/fonts/material-symbols-rounded.woff2` | Apache-2.0 (per the upstream repository's LICENSE) | https://github.com/google/material-design-icons |

These three JS files are served to the standalone HTML apps in
`dashboard/public/*.html`, which run without the Next.js bundler; `react`
and `react-dom` also appear in the JavaScript table above because the
dashboard proper installs them from npm as well.

Most dashboard icons are drawn from `@mdi/js` (Apache-2.0), already listed
in the JavaScript table; the Material Symbols Rounded ligature face above is
the one icon font served as a file, self-hosted from `dashboard/public/fonts/`
and preloaded by `app/layout.tsx`, never from Google's CDN.

---

## Bundled assets

- No 3D avatar model ships with this repository. The default avatar is Puff,
  the cloud logo (`dashboard/public/puffpai-mascot.svg` and the
  `puffpai-icon-*.png` / `puffpai-logo.png` images), made by Cypher Sisters for
  this project; it is not a third-party asset and carries the repository
  licence. A household can upload its own `.glb`; that file stays on its own
  machine and is never part of this repository.
- `dashboard/public/design-cloud-1.jpg` to `design-cloud-5.jpg` and
  `dashboard/public/favicon.png`: made by Cypher Sisters for this project,
  repository licence.

---

## Look lists (colour data)

`config/catalog_snapshots/looks_*.json` hold colour sets converted from two
free look lists. Only colour numbers, the corner radius and
whether the main font is monospace were read; each colour was re-written as
`#rrggbb` by `src/puffpai/looks/`, and the describing words in the files are
BuildBot's own. No CSS, font file or picture from either list is included.

| List | Project | Licence |
|---|---|---|
| tweakcn community looks (`https://tweakcn.com/r/themes/registry.json`) | https://github.com/jnsahaj/tweakcn | Apache-2.0 |
| shadcn/ui base looks (`https://ui.shadcn.com/r/styles/new-york-v4/registry.json`) | https://github.com/shadcn-ui/ui | MIT |

Six tweakcn looks named after companies or products were left out (listed
under `left_out` in the file).

---

## Catalog snapshots and word lists

Files in `config/catalog_snapshots/` and `config/wordlists/`. BuildBot reads
them on this computer to decide where to look and what fits a goal; none is
sent anywhere. Each `<name>.vec.npz` beside a snapshot holds meaning vectors
BuildBot computed from that snapshot's own names, descriptions and tags with
the pinned reading helper (all-MiniLM-L6-v2); it is derived data of the file
it sits beside and carries no text. Licences below were read from each
source when its snapshot was taken; "to confirm" says why one could not be.

| File | Source | Licence (as read) | Attribution | What was derived |
|---|---|---|---|---|
| `awesome_selfhosted_2026-09-27.json.gz` | https://github.com/awesome-selfhosted/awesome-selfhosted-data at commit `c10a71ae91ccc513dc4329578896810c499c4b21` | CC BY-SA 3.0 (the repository's LICENSE: "Creative Commons Attribution-ShareAlike 3.0 Unported") | Data from awesome-selfhosted-data by the awesome-selfhosted contributors | Name, description, tags, licences, platforms, addresses, stars and last change of each entry, reshaped to one JSON file. This derived snapshot is shared under the same licence, CC BY-SA 3.0, with this attribution (also in the file's header). |
| `hacs_2026-09-27.json.gz` | https://github.com/hacs/default at commit `94ae363e31684d0be81e97045e33a404e1ec5128` and https://data-v2.hacs.xyz/ (integration, plugin, theme) | hacs/default: MIT (its LICENSE, Copyright (c) 2019 HACS). data-v2.hacs.xyz: to confirm (no terms stated; its descriptions are each project's own GitHub description) | HACS | Full name, name, description, topics, stars and last change of each listed add-on. Look-only rows. |
| `apis_guru_2026-09-23.json.gz` | https://api.apis.guru/v2/list.json (APIs.guru openapi-directory) | CC0 1.0 (the openapi-directory LICENSE) | APIs.guru | Names, descriptions and addresses of API definitions. |
| `public_apis_2026-09-23.json` | https://github.com/public-apis/public-apis (README.md) | MIT (the repository's LICENSE, Copyright (c) 2022 public-apis) | public-apis contributors | Rows of the README's tables (name, description, auth, HTTPS, CORS, link). |
| `ha_integrations_2026-09-23.json` | https://github.com/home-assistant/core (homeassistant/generated/integrations.json) | Apache-2.0 (the repository's LICENSE.md) | Home Assistant authors | Integration names, brands, domains and how each connects. |
| `mcp_registry_2026-09-23.json.gz` | https://registry.modelcontextprotocol.io/v0/servers | to confirm: the registry's code is moving from MIT to Apache-2.0 (its LICENSE); no terms are stated for the published server listings | Model Context Protocol registry | Server names, descriptions and addresses. Look-only rows. |
| `kiwix_opds_eng_2026-09-23.json` | https://opds.library.kiwix.org/catalog/v2/entries?lang=eng | to confirm: the OPDS catalog states no licence for its listings | Kiwix | Listings only: names, titles, sizes, download descriptions. No book content. |
| `gutenberg_catalog_en_2026-09-24.json.gz` | https://www.gutenberg.org/cache/epub/feeds/pg_catalog.csv | to confirm: Project Gutenberg's offline catalogs page asks people to use these files in their own tools instead of crawling, but names no licence for the catalog | Project Gutenberg | English book ids, titles, authors and subjects (literature, class P, left out). |
| `openstax_books_2026-09-24.json` | https://openstax.org/apps/cms/api/v2/pages/?type=books.Book | to confirm: the books are under the Creative Commons licence each one names (kept per book in the file); the listing's own terms page needs a browser with JavaScript and could not be read | OpenStax, Rice University | Book titles, subjects, states, licence names and descriptions. |
| `looks_tweakcn_2026-09-24.json` | https://tweakcn.com/r/themes/registry.json | Apache-2.0 (https://github.com/jnsahaj/tweakcn) | tweakcn | Colour numbers, corner radius, monospace flag (see "Look lists" above). |
| `looks_shadcn_2026-09-24.json` | https://ui.shadcn.com/r/styles/new-york-v4/registry.json | MIT (https://github.com/shadcn-ui/ui) | shadcn/ui | Colour numbers, corner radius, monospace flag (see "Look lists" above). |
| `config/wordlists/en_US.dic` | en_US Hunspell dictionary 2020.12.07, SCOWL size 60, http://wordlist.aspell.net | SCOWL's own licence (read from its Copyright file and README): permission to use, copy, modify, distribute and sell for any purpose without fee, keeping the copyright and permission notice; parts from Ispell under Geoff Kuenning's BSD licence and other listed sources | Kevin Atkinson and the SCOWL contributors | Used unchanged, to tell ordinary words from names on this computer. |
| `config/where_to_look.json`, the `licence_words` part | the licence texts kept verbatim in `tests/fixtures/recorded_web/license_*.txt`: Apache License 2.0 (https://www.apache.org/licenses/LICENSE-2.0), GNU GPL 2.0 and 3.0, LGPL 2.1 and 3.0, AGPL 3.0 (https://www.gnu.org/licenses/), Mozilla Public License 2.0 (https://www.mozilla.org/MPL/2.0/), SIL Open Font License 1.1 (https://openfontlicense.org), CC0 1.0 and the Creative Commons 4.0 licences (https://creativecommons.org/licenses/) | each licence text's own terms for copying it (the FSF texts: verbatim copies may be distributed; Creative Commons: its legal code is dedicated under CC0) | the publishers named | Each text as lowercase words in order, built by `python -m puffpai.catalogs.snapshot_build_code licence`, used only to recognise a project's licence file; never shown or sent |
| `config/wordlists/NOTICE.txt` | written for this project from the dictionary's README | the notice reproduces SCOWL's copyright and permission text verbatim, as that licence requires | as above | The licence notice for `en_US.dic`. |

---

## Notes

- **Python table** is the full transitive closure actually resolved by
  pip in a scratch virtualenv against the version constraints in
  `requirements.txt` and `setup.py`'s `voice`/`dev`/`desktop` extras.
  This is what a real install pulls in, not just the direct pins.
- **JavaScript table** lists only the packages named directly in
  `dashboard/package.json` (dependencies + devDependencies), with
  version/license/homepage resolved against each package's current
  `latest` npm registry entry rather than the exact semver-range pin.
  The roughly 900 transitive packages are not listed. A full transitive JS
  notice (matching what `npm install` actually resolves, via
  `npx license-checker --summary`) is planned.
- `talkinghead` is pulled directly from GitHub, pinned to commit
  `c22aed43249f9f8cbadf9cddda5b21b48f4732b3` (2026-02-26) rather than
  floating on the default branch; license taken from that repository's
  own LICENSE file (MIT), not the npm registry.
- The **Upstream template** section covers borrowed source code, which the
  package-manager tables cannot see: neither `pip-licenses` nor the npm
  registry knows that a component was copied from a template rather than
  installed from a registry.
- This file does not alter the project's own copyright/license
  (see `LICENSE`); it lists what the project *depends on*.
