"""Feed readers. Both return a FeedResult with items in one deterministic order and a reason for every rejected entry.

  LocalFeed  reads runs/<id>/feed.jsonl, ordered by leader_seq.
  ChainFeed  reads Memo transactions of allowlisted signers over RPC, ordered by (slot, tx index, signature).
             Only signals whose FEE PAYER is on the allowlist are accepted. On any network failure it falls back to its
             disk cache and says so in `notes` (source == "cache"); it never invents data.

`tx index` is 0 when the RPC does not report one (getTransaction does not), so signature breaks ties within a slot."""

from dataclasses import dataclass, field
from pathlib import Path

import orjson

from leaders.signal import MAX_MEMO_BYTES, Signal, SignalError, parse_memo, validate
from leaders.solana import MEMO_PROGRAM, RpcClient, RpcError, b58decode


@dataclass(frozen=True)
class FeedItem:
    signal: Signal
    signer: str
    slot: int
    index: int
    signature: str

    @property
    def sort_key(self):
        return (self.slot, self.index, self.signature)


@dataclass
class FeedResult:
    items: list
    source: str                                   # "local" | "chain" | "cache" | "unavailable"
    rejected: list = field(default_factory=list)  # [(ref, reason)]
    notes: list = field(default_factory=list)


class LocalFeed:
    def __init__(self, path):
        self.path = Path(path)

    def read(self) -> FeedResult:
        items, rejected, notes, seen = [], [], [], set()
        try:
            lines = self.path.read_bytes().splitlines()
        except OSError:
            return FeedResult([], "unavailable", notes=[f"cannot read {self.path}"])
        for n, raw in enumerate(lines, 1):
            ref = f"line {n}"
            try:
                d = orjson.loads(raw)
            except orjson.JSONDecodeError:
                rejected.append((ref, "not valid JSON"))
                continue
            if isinstance(d, dict) and "event" in d:
                if d["event"] == "chain_disabled":
                    notes.append(f"chain publishing was off from leader_seq {d.get('before_leader_seq')}: {d.get('reason')}")
                continue
            try:
                sig = validate(d.get("signal") if isinstance(d, dict) else None)
            except SignalError as e:
                rejected.append((ref, str(e)))
                continue
            if sig.leader_seq in seen:
                rejected.append((ref, f"duplicate leader_seq {sig.leader_seq}"))
                continue
            seen.add(sig.leader_seq)
            items.append(FeedItem(sig, "local", 0, sig.leader_seq, d.get("tx") or ""))
        items.sort(key=lambda i: i.sort_key)
        return FeedResult(items, "local", rejected, notes + ["local feed: not signature-verified"])


class ChainFeed:
    def __init__(self, rpc: RpcClient, allowlist, cache_path, limit=1000):
        self.rpc, self.allow, self.cache, self.limit = rpc, tuple(sorted(set(allowlist))), Path(cache_path), limit

    def read(self) -> FeedResult:
        try:
            result = self._read_chain()
        except RpcError as e:
            return self._from_cache(f"network failure ({e})")
        self._write_cache(result)
        return result

    def _read_chain(self) -> FeedResult:
        items, rejected, seen_sig, seen_seq = [], [], set(), set()
        for addr in self.allow:
            entries = self.rpc.call("getSignaturesForAddress", [addr, {"limit": self.limit}])
            for e in entries if isinstance(entries, list) else []:
                sig = e.get("signature") if isinstance(e, dict) else None
                if not isinstance(sig, str) or sig in seen_sig:
                    continue
                seen_sig.add(sig)
                if e.get("err") is not None:
                    rejected.append((sig, "transaction failed on chain"))
                    continue
                tx = self.rpc.call("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
                if tx is None:
                    raise RpcError("transaction not available yet")
                try:
                    items.append(self._parse(sig, e, tx))
                except SignalError as err:
                    rejected.append((sig, str(err)))
        items.sort(key=lambda i: i.sort_key)
        kept = []
        for it in items:  # first in chain order wins a duplicate leader_seq from the same signer
            k = (it.signer, it.signal.leader_seq)
            if k in seen_seq:
                rejected.append((it.signature, f"duplicate leader_seq {it.signal.leader_seq}"))
            else:
                seen_seq.add(k)
                kept.append(it)
        rejected.sort()
        return FeedResult(kept, "chain", rejected, [f"read from {len(self.allow)} allowlisted signer(s), up to {self.limit} txs each"])

    def _parse(self, sig, entry, tx) -> FeedItem:
        try:
            msg = tx["transaction"]["message"]
            keys = msg["accountKeys"]
            payer = keys[0]["pubkey"] if isinstance(keys[0], dict) else keys[0]
            if tx["transaction"]["signatures"][0] != sig:
                raise SignalError("signature does not match transaction")
            if (tx.get("meta") or {}).get("err") is not None:
                raise SignalError("transaction failed on chain")
            memos = [i for i in msg["instructions"] if i.get("programId") == MEMO_PROGRAM]
        except (KeyError, IndexError, TypeError, AttributeError):
            raise SignalError("transaction not in the expected shape") from None
        if payer not in self.allow:
            raise SignalError("signer is not on the allowlist")
        if len(memos) != 1:
            raise SignalError("expected exactly one memo instruction, found %d" % len(memos))
        m = memos[0]
        raw = m.get("parsed") if isinstance(m.get("parsed"), str) else None
        if raw is None:
            try:
                raw = b58decode(m["data"]).decode()
            except (KeyError, ValueError, UnicodeDecodeError, TypeError):
                raise SignalError("memo data unreadable") from None
        if len(raw.encode()) > MAX_MEMO_BYTES:
            raise SignalError("memo is oversized")
        slot = tx.get("slot", entry.get("slot"))
        if type(slot) is not int:
            raise SignalError("missing slot")
        index = tx.get("index", 0) if type(tx.get("index", 0)) is int else 0
        return FeedItem(parse_memo(raw), payer, slot, index, sig)

    def _write_cache(self, r: FeedResult):
        import time
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        body = {"retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "items": [[i.signal.to_dict(), i.signer, i.slot, i.index, i.signature] for i in r.items]}
        tmp = self.cache.with_name(self.cache.name + ".tmp")
        tmp.write_bytes(orjson.dumps(body))
        tmp.replace(self.cache)

    def _from_cache(self, why) -> FeedResult:
        try:
            d = orjson.loads(self.cache.read_bytes())
            items = [FeedItem(validate(s), signer, slot, idx, sg) for s, signer, slot, idx, sg in d["items"]]
        except (OSError, KeyError, TypeError, ValueError, SignalError):
            return FeedResult([], "unavailable", notes=[f"{why}; no usable cache at {self.cache}"])
        items.sort(key=lambda i: i.sort_key)
        return FeedResult(items, "cache", notes=[f"{why}; showing CACHED data retrieved {d.get('retrieved_at')}, may be stale"])
