#!/usr/bin/env bash
# Start the gate in a container limited to 1 CPU and 512 MB (no swap beyond the limit). The load
# generator runs on the host, OUTSIDE the container, so it does not take the gate's CPU:
#   bash scripts/run_gate_docker.sh batched 9101
#   .venv/bin/python -m client --fixture fixtures/orders.jsonl --port 9101
# The log is on a Docker named volume (the Docker Desktop VM's own ext4 disk), not a macOS bind
# mount: a bind mount would send fsync through virtiofs to the host filesystem. The full benchmark
# is `python -m bench.run --backend docker --label docker` (it manages containers itself).
# On macOS the container runs in a Linux VM, so fsync cost reflects the VM's disk, not native macOS.
set -euo pipefail
FSYNC=${1:-batched}; PORT=${2:-9101}
# bench rules come from `python -m bench.run` (bench/data/rules_bench.yaml); fall back to rules.yaml
RULES=bench/data/rules_bench.yaml; [ -f "$RULES" ] || RULES=rules.yaml
docker build -t trade-seatbelt-gate .
docker volume create tsb-gate-data >/dev/null
docker run --rm --name tsb-gate --cpus=1 --memory=512m --memory-swap=512m \
  -p "127.0.0.1:$PORT:9101" -v tsb-gate-data:/data -v "$PWD/$RULES:/app/rules_bench.yaml:ro" \
  trade-seatbelt-gate --rules rules_bench.yaml --log /data/gate.log --fsync "$FSYNC"
# the log persists in the volume `tsb-gate-data`; remove it with: docker volume rm tsb-gate-data
