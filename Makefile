PYTHON312 ?= $(shell command -v python3.12 || echo /opt/homebrew/bin/python3.12)
PY = .venv/bin/python

.PHONY: leaders-setup setup sim demo demo-recon test bench bench-docker bench-report clean api web web-build web-test

setup:
	$(PYTHON312) -m venv .venv
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -e ".[dev,api,sim,leaders]"

demo:
	bash scripts/demo.sh

# Headless simulated trader against its own gate in runs/<id>/ (offline prices unless SIM_PROVIDER says otherwise).
#   make sim SIM_ORDERS=500 SIM_PROFILE=tilt SIM_SEED=42 SIM_PROVIDER=yfinance
sim:
	$(PY) -m sim --provider $(or $(SIM_PROVIDER),offline) --profile $(or $(SIM_PROFILE),disciplined) --orders $(or $(SIM_ORDERS),300) --speed $(or $(SIM_SPEED),50) $(if $(SIM_SEED),--seed $(SIM_SEED))

# Throwaway devnet keypair + airdrop for the leaders' signal publisher (devnet only; refuses any other RPC)
leaders-setup:
	$(PY) -m leaders setup

demo-recon:
	bash scripts/demo_recon.sh

test:
	$(PY) -m pytest -q --junitxml=out/pytest-junit.xml   # the landing page reads test status from this file

# API for the dashboard: 127.0.0.1:8000 only, read-only (see api/app.py)
api:
	$(PY) -m api

# Dashboard dev server on http://127.0.0.1:5173 (proxies /api to the API; start `make api` in another terminal)
web:
	cd web && ([ -d node_modules ] || npm ci) && npm run dev

web-build:
	cd web && ([ -d node_modules ] || npm ci) && npm run build

web-test:
	cd web && ([ -d node_modules ] || npm ci) && npm test

clean:
	rm -rf out .pytest_cache .hypothesis

bench:
	$(PY) -m bench.run --orders $(or $(BENCH_N),100000) --reps $(or $(BENCH_REPS),3) --label final
	$(PY) -m bench.report   # writes bench/results/PERFORMANCE_latest.md, never docs/PERFORMANCE.md

bench-docker:
	docker build -t trade-seatbelt-gate .
	$(PY) -m bench.run --backend docker --orders $(or $(BENCH_N),100000) --reps $(or $(BENCH_REPS),3) --label docker
	$(PY) -m bench.report   # writes bench/results/PERFORMANCE_latest.md, never docs/PERFORMANCE.md

# Explicitly regenerate the committed docs/PERFORMANCE.md from chosen result files:
#   make bench-report FINAL=bench/results/final_<stamp>.json [DOCKER=bench/results/docker_<stamp>.json]
bench-report:
	@test -n "$(FINAL)" || { echo "usage: make bench-report FINAL=bench/results/final_<stamp>.json [DOCKER=bench/results/docker_<stamp>.json]"; exit 1; }
	$(PY) -m bench.report --native-file $(FINAL) $(if $(DOCKER),--docker-file $(DOCKER)) --out docs/PERFORMANCE.md
