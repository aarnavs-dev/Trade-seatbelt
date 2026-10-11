"""Publishes signals: always to runs/<id>/feed.jsonl, and (when a devnet RPC and keypair are given) as Memo transactions,
at most one per second, with exponential backoff. If the chain path keeps failing it is switched off for the rest of
the run and every later line is labelled "local-only"; the run itself never fails because of the network.
A non-devnet RPC URL is refused up front (MainnetWriteRefused); that is never swallowed."""

import time
from pathlib import Path

import orjson

from leaders.signal import Signal
from leaders.solana import RpcClient, RpcError, assert_devnet, b64, memo_transaction, require_devnet


class Publisher:
    def __init__(self, run_dir, rpc: RpcClient | None = None, keypair=None, *, min_interval=1.0, max_retries=4,
                 base_backoff=1.0, clock=time.monotonic, sleep=time.sleep):
        if rpc is not None:
            require_devnet(rpc.url)
        self.rpc, self.kp = (rpc, keypair) if rpc is not None and keypair is not None else (None, None)
        self.path = Path(run_dir) / "feed.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.min_interval, self.max_retries, self.base = min_interval, max_retries, base_backoff
        self._clock, self._sleep, self._last = clock, sleep, None
        self._checked = False
        self.chain_ok = self.rpc is not None
        self.disabled_reason = None if self.chain_ok else "no devnet RPC/keypair configured"

    def _append(self, obj):
        with open(self.path, "ab") as f:
            f.write(orjson.dumps(obj, option=orjson.OPT_SORT_KEYS) + b"\n")

    def _throttle(self):
        if self._last is not None:
            wait = self._last + self.min_interval - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()

    def _send(self, memo: bytes) -> str:
        if not self._checked:
            assert_devnet(self.rpc)       # MainnetWriteRefused propagates; RpcError means "chain unavailable"
            self._checked = True
        delay = self.base
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                bh = self.rpc.call("getLatestBlockhash")["value"]["blockhash"]
                tx, sig = memo_transaction(self.kp, memo, bh)
                self.rpc.call("sendTransaction", [b64(tx), {"encoding": "base64"}])
                return sig
            except (RpcError, KeyError, TypeError, ValueError) as e:
                if attempt == self.max_retries:
                    raise RpcError(f"gave up after {attempt + 1} attempts: {e}") from None
                self._sleep(delay)
                delay *= 2
        raise AssertionError("unreachable")

    def publish(self, sig: Signal) -> dict:
        tx = None
        if self.chain_ok:
            try:
                tx = self._send(sig.to_memo())
            except RpcError as e:
                self.chain_ok, self.disabled_reason = False, str(e)[:200]
                self._append({"event": "chain_disabled", "reason": self.disabled_reason, "before_leader_seq": sig.leader_seq})
        line = {"signal": sig.to_dict(), "via": "chain" if tx else "local-only", "tx": tx}
        self._append(line)
        return line
