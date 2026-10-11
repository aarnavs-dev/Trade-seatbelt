"""Run the API. Binds to 127.0.0.1 only; there is deliberately no --host option."""

import argparse

import uvicorn

if __name__ == "__main__":
    p = argparse.ArgumentParser(prog="api")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()
    uvicorn.run("api.app:create_app", factory=True, host="127.0.0.1", port=args.port, log_level="warning")
