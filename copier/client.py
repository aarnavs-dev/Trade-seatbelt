"""Blocking newline-delimited-JSON client for the gate (the same protocol as client/ and sim/)."""

import socket

import orjson


class GateClient:
    def __init__(self, port, host="127.0.0.1", timeout=15.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.f = self.sock.makefile("rwb")

    def submit(self, order: dict) -> dict:
        self.f.write(orjson.dumps(order) + b"\n")
        self.f.flush()
        raw = self.f.readline()
        if not raw:
            raise ConnectionError("the gate closed the connection")
        return orjson.loads(raw)

    def close(self):
        try:
            self.f.close()
        finally:
            self.sock.close()
