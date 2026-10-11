# Disclosure

## This project's license

MIT, Copyright (c) 2026 aarnav.dev — see the [LICENSE](../LICENSE) file in the repository root.

## What we built (this project's own code)

`core/`, `gate/`, `auditor/`, `client/`, `api/`, `web/`, `fixtures/`, `bench/`, `tests/`, `scripts/`, the `Makefile`, `Dockerfile` and
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
| FastAPI | 0.143.0 | `api/` (dashboard backend) | MIT |
| Starlette | 1.7.0 | (FastAPI dependency) request/response, SSE streaming, test client | BSD-3-Clause |
| uvicorn | 0.54.0 | serves `api/` on 127.0.0.1 | BSD-3-Clause |
| h11 / click | 0.16.0 / 8.5.0 | (uvicorn dependencies) | MIT / BSD-3-Clause |
| anyio / idna | 4.15.1 / 3.20 | (Starlette / httpx dependencies) | MIT / BSD-3-Clause |
| annotated-doc | 0.0.5 | (FastAPI dependency) | MIT |
| httpx / httpcore | 0.28.1 / 1.0.9 | API tests only (`tests/test_api.py`, via Starlette's test client) | BSD-3-Clause / BSD-3-Clause |
| certifi | 2026.7.22 | (httpx dependency) | MPL-2.0 |

**Simulator extra (`pip install -e ".[sim]"`, `sim/`).** Only needed for live market-data snapshots; the built-in offline provider needs none of
these. Versions and licenses read from each installed package's metadata (`importlib.metadata`) on 2026-10-11:

| Package | Version | Used for | License per package metadata |
|---|---|---|---|
| yfinance | 1.7.0 | optional read-only price snapshot, fetched once per run. **Unofficial** wrapper over Yahoo Finance endpoints; it may rate-limit, change or break, and Yahoo's own terms apply to the data (see the terms note in the Tier 5 docs update) | Apache Software License |
| pandas / numpy | 3.0.6 / 2.5.4 | (yfinance dependencies) | BSD License / `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0` |
| curl-cffi / cffi / pycparser | 0.16.3 / 2.1.1 / 3.11 | (yfinance dependencies) HTTP client | MIT / MIT-0 / BSD-3-Clause |
| requests / urllib3 / charset-normalizer | 2.34.2 / 2.8.0 / 3.5.2 | (yfinance dependencies) | Apache Software License / MIT / MIT |
| beautifulsoup4 / soupsieve / lxml | 4.15.0 / 3.0.1 / 6.1.3 | (yfinance dependencies) | MIT / MIT / BSD-3-Clause |
| multitasking / platformdirs / websockets / protobuf | 0.0.13 / 4.13.0 / 17.2 / 7.36.2 | (yfinance dependencies) | Apache Software License / MIT / BSD-3-Clause / 3-Clause BSD License |
| peewee | 4.5.3 | (yfinance dependency) | not stated in its package metadata; not verified here |
| pytz / python-dateutil / six | 2026.5 / 2.9.0.post0 / 1.17.0 | (pandas / yfinance dependencies) | MIT / BSD License + Apache Software License / MIT |
| httpx | 0.28.1 | Alpaca Market Data requests (`sim/providers.py`), already listed above for the API tests | BSD-3-Clause |

Alpaca Market Data is used only if `ALPACA_KEY_ID` and `ALPACA_SECRET_KEY` are set in `.env` / the environment. It is read-only (latest trade),
the code has no order endpoint, and the keys are never logged or written to a run directory. The Alpaca path has only been tested against a fake
HTTP client, never against the live service.

**Front end (`web/`, npm).** Versions and licenses are read from `web/package.json` and each installed package's own `package.json`
(`license` field) in `web/node_modules`. The UI was rebuilt twice: first to a light theme without 3D (Tier A), then restyled back to
a dark theme with a 3D chain (this version). Tailwind and `framer-motion` were dropped and are not used: the pages are plain CSS on
design tokens (CSS variables); the only motion libraries are three.js and React Three Fiber, and the 150 ms row fade is CSS.

Runtime dependencies (they ship to the browser; the three.js chunk is loaded only on pages that show a 3D scene):

| Package | Version | Used for | License per package.json |
|---|---|---|---|
| three | 0.186.1 | 3D rendering of the chain scenes (lazy chunk) | MIT |
| @react-three/fiber | 9.8.1 | React renderer for three.js | MIT |
| @react-three/drei | 10.7.9 | helpers; only `PerformanceMonitor` is imported | MIT |
| @fontsource-variable/newsreader | 5.3.0 | headline serif (Newsreader, Production Type), bundled | OFL-1.1 |
| @fontsource-variable/inter | 5.3.0 | UI sans (Inter, Rasmus Andersson), bundled | OFL-1.1 |
| @fontsource-variable/jetbrains-mono | 5.3.0 | monospace for numbers, hashes and addresses (JetBrains Mono), bundled | OFL-1.1 |
| react | 19.3.0 | UI | MIT |
| react-dom | 19.3.0 | UI | MIT |

All three fonts are self-hosted from the bundle (Fontsource packages), so no page makes a request to a font CDN. Each font is licensed
under the SIL Open Font License 1.1, which allows bundling and redistribution; the license text is in each package's `LICENSE` file.
The 3D scenes draw plain geometry and text labels: no Solana logo, brand colors or third-party artwork is used; "Solana" appears as text only.

Transitive runtime packages (56, installed beneath the ones above, mostly by `@react-three/drei`; read the same way): MIT 46, Apache-2.0 5,
ISC 3, BSD-3-Clause 1, and one with no `license` field in its `package.json`: `webgl-constants` 1.1.1, whose `LICENSE` file (read directly) is the MIT license.

Development dependencies (build, test and the Playwright check; none ships to the browser):

| Package | Version | License per package.json |
|---|---|---|
| @types/node | 26.6.5 | MIT |
| @types/react | 19.3.0 | MIT |
| @types/react-dom | 19.3.0 | MIT |
| @types/three | 0.186.0 | MIT |
| @vitejs/plugin-react | 6.1.2 | MIT |
| playwright | 1.64.0 | Apache-2.0 |
| typescript | 7.0.2 | Apache-2.0 |
| vite | 8.3.4 | MIT |
| vitest | 4.1.11 | MIT |

The 45 further packages installed beneath those (development tooling; read the same way): MIT 38, Apache-2.0 4, ISC 2, BSD-3-Clause 1.
Platform-specific optional binaries that npm does not install on every machine are not counted. The charting library for the
dashboard (uPlot) is not installed yet; it is added with the dashboard page.

Playwright's Chromium (downloaded by `npx playwright install chromium`) is used only for the screenshot check; its license was not read from metadata here.

setuptools is the build backend named in `pyproject.toml`; it is not installed in the virtualenv, so its license was not checked.

**Other tools — licenses NOT read from package metadata** (these are not Python packages; stated from general knowledge, not verified here):

| Tool | Used for | License as we understand it |
|---|---|---|
| Python 3.12 (standard library: `asyncio`, `hashlib`, `cProfile`, `fcntl`, ...) | runtime | PSF License |
| GNU Make 3.81 (the version macOS ships) | task runner | GPL-2.0-or-later (a tool, not distributed with this project) |
| Node.js 20.20.2 / npm 10.8.2 | runs the front-end toolchain | MIT (Node) / Artistic-2.0 (npm); not read from metadata here |
| Docker Desktop 4.94 / Docker Engine 29.8.2 | benchmark under CPU / memory limits | Docker Desktop: Docker's subscription terms; Engine: Apache-2.0 |
| `python:3.12-slim` image | base of the benchmark container | Python: PSF; Debian packages: various |
| Homebrew | installing Python / Docker on the dev machine | BSD-2-Clause |

## Not used

No external API or service is called. The following are described in the project brief as optional extras and are
**not built or integrated**: Gemini API, ElevenLabs, Tiger Data (TimescaleDB). (FastAPI and the React front end *are* built; see above.) The application runs fully offline and needs no keys; `.env.example` lists placeholders for them only.

## Data

All orders and logs are generated (seeded). No real market data, no personal financial data, no broker credentials.

## Copy-trading layer (`marketdata/`, `leaders/`)

| Item | Use | Notes |
|---|---|---|
| Alpaca Market Data (historical bars, IEX feed) | replay data if `ALPACA_KEY_ID`/`ALPACA_SECRET_KEY` are set | data endpoint only; no order code exists |
| yfinance (unofficial Yahoo wrapper) | replay data without keys | may break or rate-limit; limited intraday history |
| Solana **devnet** (public RPC, faucet, Memo program) | optional signal publishing | throwaway keypair, no value; writes refuse non-devnet URLs |
| `cryptography` (Ed25519) | signs devnet transactions | BSD/Apache-2.0 |

Live Alpaca, xStocks and Pyth are disabled stubs that make no network calls. Signals are paper only.

## Follower (`copier/`, `auditor/explain.py`)

No new service or key. The follower is a normal TCP client of the gate and reads the same market-data providers as the
leaders (Alpaca or yfinance, historical bars only). When no real data can be fetched it uses a **SYNTHETIC FIXTURE**
(`fixtures/synthetic_bars/`, generated by `fixtures/gen_synthetic_bars.py`, not market data); the label is printed and
stored in the run manifest. Explanations are deterministic text built from the logs, not AI. Fills are paper.

`make follow-chain` and `make devnet-check` use the same Solana **devnet** public RPC and Memo program as the leaders (throwaway
keypair in `.keys/`, gitignored, secret never printed; writes refuse non-devnet URLs). `devnet-check` publishes 10 seeded,
meaningless signals; they stay public on devnet. No new dependency.

The `/follow` page and `api/follow.py` add no service, key or dependency: the API starts the local copier as a
subprocess and reads the run's files. Explanations are deterministic text, not AI. Execution is paper only.

The `/rules` page stores your four limits in a runtime profile file (`profile/rules.json`, gitignored); `/follow` adds session limits,
copy sizing and a seeded pace, all local to the copier. The landing hero is a three.js scene (sample signals with real SHA-256 hashes, labelled as an illustration); its 2D fallback is plain CSS/SVG. Signals are published
to Solana devnet (a test network); fills are paper. No AI is used anywhere in these pages.
