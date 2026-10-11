PYTHON312 ?= $(shell command -v python3.12 || echo /opt/homebrew/bin/python3.12)
PY = .venv/bin/python

.PHONY: setup demo test bench clean

setup:
	$(PYTHON312) -m venv .venv
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -e ".[dev]"

demo:
	bash scripts/demo.sh

test:
	$(PY) -m pytest -q

clean:
	rm -rf out .pytest_cache .hypothesis

bench:
	$(PY) -m bench.run --orders $(or $(BENCH_N),100000) --reps $(or $(BENCH_REPS),3) --label final
	$(PY) -m bench.report
