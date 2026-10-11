# Gate only. Run it with limits (see scripts/run_gate_docker.sh); the load generator stays outside.
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml rules.yaml ./
COPY core ./core
COPY gate ./gate
RUN pip install --no-cache-dir orjson pydantic pyyaml uvloop
ENV PYTHONUNBUFFERED=1
EXPOSE 9101
ENTRYPOINT ["python", "-m", "gate", "--host", "0.0.0.0", "--port", "9101"]
CMD ["--rules", "rules.yaml", "--log", "/data/gate.log", "--fsync", "batched"]
