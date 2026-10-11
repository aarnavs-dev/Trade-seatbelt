# Disclosure

## What we built (this project's own code)

`core/`, `gate/`, `auditor/`, `client/`, `fixtures/`, `bench/`, `tests/`, `scripts/`, the `Makefile`, `Dockerfile` and
these docs. The design (hash-chained log, pure `check()` shared by gate and auditor, single-writer gate with
group commit, benchmark verification against a reference model) is ours.

**Claude Code was used to build this project.** The code, tests, benchmark harness and documentation were written
with Claude Code (model: Claude Sonnet 5.5) working in this repository under the team's direction; its output was run
and tested here. Numbers in `docs/PERFORMANCE.md` come from running the benchmark, not from the model.

## Libraries and tools used

| Name | Used for | License (as published by the project) |
|---|---|---|
| Python 3.12 | runtime | PSF License |
| orjson | canonical JSON, fast (de)serialisation | Apache-2.0 OR MIT |
| pydantic v2 | strict event / order / rules models | MIT |
| PyYAML | loading `rules.yaml` | MIT |
| uvloop | event loop for the gate and load generator | Apache-2.0 OR MIT |
| pytest | tests | MIT |
| Hypothesis | property-based tests | MPL-2.0 |
| psutil | benchmark CPU / memory sampling | BSD-3-Clause |
| GNU Make | task runner | GPL-3.0 (a tool, not distributed with this project) |
| Docker (Docker Desktop, Docker Engine), `python:3.12-slim` image | benchmark under CPU / memory limits | Docker Desktop: Docker's subscription terms; Engine: Apache-2.0; image: PSF + Debian licenses |
| Homebrew | installing Python / Docker on the dev machine | BSD-2-Clause |
| Python standard library (`asyncio`, `hashlib`, `cProfile`, `pstats`, ...) | everywhere | PSF License |

Licenses are listed from what we know of each project and were not re-verified against each release.

## Not used

No external API or service is called. The following are described in the project brief as optional extras and are
**not built or integrated**: Gemini API, ElevenLabs, Tiger Data (TimescaleDB), FastAPI, and the React / three.js
front end. The application runs fully offline and needs no keys; `.env.example` lists placeholders for them only.

## Data

All orders and logs are generated (seeded). No real market data, no personal financial data, no broker credentials.
