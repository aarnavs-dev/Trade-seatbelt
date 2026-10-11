PYTHON312 ?= $(shell command -v python3.12 || echo /opt/homebrew/bin/python3.12)
PY = .venv/bin/python

.PHONY: setup demo demo-recon test bench bench-docker bench-report clean api web web-build

setup:
	$(PYTHON312) -m venv .venv
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -e ".[dev,api]"

demo:
	bash scripts/demo.sh

demo-recon:
	bash scripts/demo_recon.sh

test:
	$(PY) -m pytest -q

# API for the dashboard: 127.0.0.1:8000 only, read-only (see api/app.py)
api:
	$(PY) -m api

# Dashboard dev server on http://127.0.0.1:5173 (proxies /api to the API; start `make api` in another terminal)
web:
	cd web && ([ -d node_modules ] || npm ci) && npm run dev

web-build:
	cd web && ([ -d node_modules ] || npm ci) && npm run build

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
