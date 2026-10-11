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

**Front end (`web/`, npm).** Versions and licenses below are read from `web/package.json` and from each installed package's own `package.json` (`license` field) in `web/node_modules`, resolved by `npm ls --all`; nothing is from memory.

Direct runtime dependencies:

| Package | Version | Used for | License per package.json |
|---|---|---|---|
| @fontsource-variable/inter | 5.3.0 | UI typeface, bundled (no network fonts) | OFL-1.1 |
| @fontsource-variable/jetbrains-mono | 5.3.0 | monospace for numbers and hashes, bundled | OFL-1.1 |
| @react-three/drei | 10.7.9 | helpers; only `PerformanceMonitor` is imported | MIT |
| @react-three/fiber | 9.8.1 | React renderer for three.js | MIT |
| @types/react | 19.3.0 |  | MIT |
| @types/three | 0.186.0 |  | MIT |
| framer-motion | 14.1.0 | panel/drawer motion (LazyMotion, `domAnimation`) | MIT |
| react | 19.3.0 | UI | MIT |
| react-dom | 19.3.0 | UI | MIT |
| three | 0.186.1 | 3D rendering (lazy chunk) | MIT |
| three | 0.170.0 | 3D rendering (lazy chunk) | MIT |

Direct development dependencies (build, test and the Playwright check; none ships to the browser):

| Package | Version | License per package.json |
|---|---|---|
| @tailwindcss/vite | 4.3.3 | MIT |
| @types/node | 26.6.5 | MIT |
| @types/react-dom | 19.3.0 | MIT |
| @vitejs/plugin-react | 6.1.2 | MIT |
| playwright | 1.64.0 | Apache-2.0 |
| tailwindcss | 4.3.3 | MIT |
| typescript | 7.0.2 | Apache-2.0 |
| vite | 8.3.4 | MIT |
| vitest | 4.1.11 | MIT |

The 65 further development-only packages installed beneath those (read the same way): MIT 52, Apache-2.0 5, MPL-2.0 4, ISC 3, BSD-3-Clause 1. Platform-specific optional binaries that npm did not install on this machine were not checked.

Transitive runtime packages (55, installed as dependencies of the ones above; `@react-three/drei` brings most of them and the production bundle tree-shakes the unused parts out):

| Package | Version | License per package.json |
|---|---|---|
| @babel/runtime | 7.29.10 | MIT |
| @mediapipe/tasks-vision | 0.10.17 | Apache-2.0 |
| @monogrid/gainmap-js | 3.4.0 | MIT |
| @types/draco3d | 1.4.10 | MIT |
| @types/offscreencanvas | 2019.7.3 | MIT |
| @types/react-reconciler | 0.28.9 | MIT |
| @types/webxr | 0.5.24 | MIT |
| @use-gesture/core | 10.3.1 | MIT |
| @use-gesture/react | 10.3.1 | MIT |
| base64-js | 1.5.1 | MIT |
| bidi-js | 1.1.0 | MIT |
| buffer | 6.0.3 | MIT |
| camera-controls | 3.1.0 | MIT |
| cross-env | 7.0.3 | MIT |
| cross-spawn | 7.0.6 | MIT |
| detect-gpu | 5.0.70 | MIT |
| draco3d | 1.5.7 | Apache-2.0 |
| fflate | 0.6.11 | MIT |
| glsl-noise | 0.0.0 | MIT |
| hls.js | 1.7.3 | Apache-2.0 |
| ieee754 | 1.2.1 | BSD-3-Clause |
| immediate | 3.0.6 | MIT |
| is-promise | 2.2.2 | MIT |
| isexe | 2.0.0 | ISC |
| its-fine | 2.1.1 | MIT |
| lie | 3.3.0 | MIT |
| maath | 0.10.8 | MIT |
| meshline | 3.3.1 | MIT |
| motion-dom | 14.1.0 | MIT |
| motion-utils | 14.1.0 | MIT |
| path-key | 3.1.1 | MIT |
| potpack | 1.0.2 | ISC |
| promise-worker-transferable | 1.0.4 | Apache-2.0 |
| react-use-measure | 2.1.7 | MIT |
| require-from-string | 2.0.2 | MIT |
| scheduler | 0.28.0 | MIT |
| shebang-command | 2.0.0 | MIT |
| shebang-regex | 3.0.0 | MIT |
| stats-gl | 2.4.2 | MIT |
| stats.js | 0.17.0 | MIT |
| suspend-react | 0.1.3 | MIT |
| three-mesh-bvh | 0.8.3 | MIT |
| three-stdlib | 2.36.1 | MIT |
| troika-three-text | 0.52.5 | MIT |
| troika-three-utils | 0.52.5 | MIT |
| troika-worker-utils | 0.52.0 | MIT |
| tslib | 2.8.1 | 0BSD |
| tunnel-rat | 0.1.2 | MIT |
| use-sync-external-store | 1.7.0 | MIT |
| utility-types | 3.11.0 | MIT |
| webgl-constants | 1.1.1 | MIT (LICENSE file; package.json has no `license` field) |
| webgl-sdf-generator | 1.1.1 | MIT |
| which | 2.0.2 | ISC |
| zustand | 4.5.7 | MIT |
| zustand | 5.0.15 | MIT |

The fonts (Inter, JetBrains Mono) are OFL-1.1 and are served from the bundle, so the dashboard makes no request to a font CDN.

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
**not built or integrated**: Gemini API, ElevenLabs, Tiger Data (TimescaleDB). (FastAPI and the React / three.js front end *are* built; see above.) The application runs fully offline and needs no keys; `.env.example` lists placeholders for them only.

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

The `/app/follow` page and `api/follow.py` add no service, key or dependency: the API starts the local copier as a
subprocess and reads the run's files. Explanations are deterministic text, not AI. Execution is paper only.
