# Disclosure

## This project's license

MIT, Copyright (c) 2026 aarnav.dev — see the [LICENSE](../LICENSE) file in the repository root.

## What we built (this project's own code)

`core/`, `gate/`, `auditor/`, `client/`, `fixtures/`, `bench/`, `tests/`, `scripts/`, the `Makefile`, `Dockerfile` and
these docs. The design (hash-chained log, pure `check()` shared by gate and auditor, single-writer gate with
group commit, benchmark verification against a reference model) is ours.

**Claude Code was used to build this project.** The code, tests, benchmark harness and documentation were written
with Claude Code (model: Claude Sonnet 5.5) working in this repository under the team's direction; its output was run
and tested here. Numbers in `docs/PERFORMANCE.md` come from running the benchmark, not from the model.

## Libraries and tools used

**Python packages** (licenses and versions read from each installed package's own metadata with
`importlib.metadata` in this project's virtualenv, which is also what the Docker image installs — same versions):

| Package | Version | Used for | License per package metadata |
|---|---|---|---|
| orjson | 3.13.0 | canonical JSON, fast (de)serialisation | `MPL-2.0 AND (Apache-2.0 OR MIT)` |
| pydantic | 2.14.0 | strict event / order / rules models | MIT |
| pydantic-core | 2.50.0 | (pydantic dependency) | MIT |
| annotated-types | 0.8.0 | (pydantic dependency) | MIT |
| typing-inspection | 0.4.4 | (pydantic dependency) | MIT |
| typing-extensions | 4.16.0 | (pydantic dependency) | PSF-2.0 |
| PyYAML | 6.0.3 | loading `rules.yaml` | MIT |
| uvloop | 0.23.0 | event loop for the gate and load generator | `MIT License` in its License field; its classifiers list both Apache Software License and MIT License |
| pytest | 9.1.1 | tests | MIT |
| pluggy / iniconfig | 1.6.0 / 2.3.1 | (pytest dependencies) | MIT / MIT |
| packaging | 26.3 | (pytest dependency) | `Apache-2.0 OR BSD-2-Clause` |
| pygments | 2.21.0 | (pytest dependency) | BSD-2-Clause |
| Hypothesis | 6.168.5 | property-based tests | MPL-2.0 |
| sortedcontainers | 2.4.0 | (Hypothesis dependency) | Apache 2.0 |
| psutil | 7.2.2 | benchmark CPU / memory sampling | BSD-3-Clause |

setuptools is the build backend named in `pyproject.toml`; it is not installed in the virtualenv, so its license was not checked.

**Other tools — licenses NOT read from package metadata** (these are not Python packages; stated from general knowledge, not verified here):

| Tool | Used for | License as we understand it |
|---|---|---|
| Python 3.12 (standard library: `asyncio`, `hashlib`, `cProfile`, `fcntl`, ...) | runtime | PSF License |
| GNU Make 3.81 (the version macOS ships) | task runner | GPL-2.0-or-later (a tool, not distributed with this project) |
| Docker Desktop 4.94 / Docker Engine 29.8.2 | benchmark under CPU / memory limits | Docker Desktop: Docker's subscription terms; Engine: Apache-2.0 |
| `python:3.12-slim` image | base of the benchmark container | Python: PSF; Debian packages: various |
| Homebrew | installing Python / Docker on the dev machine | BSD-2-Clause |

## Not used

No external API or service is called. The following are described in the project brief as optional extras and are
**not built or integrated**: Gemini API, ElevenLabs, Tiger Data (TimescaleDB), FastAPI, and the React / three.js
front end. The application runs fully offline and needs no keys; `.env.example` lists placeholders for them only.

## Data

All orders and logs are generated (seeded). No real market data, no personal financial data, no broker credentials.
