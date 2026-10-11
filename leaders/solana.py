"""Just enough Solana for memo signals: base58, a Solana-CLI-format keypair file, a legacy Memo-program transaction,
and a JSON-RPC client. WRITES ARE DEVNET-ONLY: the RPC client refuses any write method unless the URL looks like devnet
(hostname check, enforced here at the lowest layer), and the publisher also checks the cluster's genesis hash.
Secret key bytes are never printed, logged or put in an error message."""

import base64
import os
import stat
from pathlib import Path
from urllib.parse import urlparse

import orjson

ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
MEMO_PROGRAM = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
DEVNET_GENESIS = "EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG"
DEFAULT_RPC = "https://api.devnet.solana.com"
RATE_LIMIT_TRIES = 5
WRITE_METHODS = frozenset({"sendTransaction", "requestAirdrop"})


class RpcError(Exception):
    pass


class MainnetWriteRefused(RuntimeError):
    pass


def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = ALPHABET[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + out


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        i = ALPHABET.find(ch)
        if i < 0:
            raise ValueError("bad base58 character")
        n = n * 58 + i
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return b"\0" * (len(s) - len(s.lstrip("1"))) + body


def is_devnet_url(url: str) -> bool:
    try:
        u = urlparse(url)
        host = (u.hostname or "").lower()
    except ValueError:
        return False
    if u.scheme not in ("http", "https") or not host:
        return False
    return any(label == "devnet" or label.startswith("devnet-") or label.endswith("-devnet")
               for label in host.split("."))


def require_devnet(url: str) -> None:
    if not is_devnet_url(url):
        raise MainnetWriteRefused("refusing to write: SOLANA_RPC_URL is not a devnet URL "
                                  "(this project only ever writes to devnet)")


class Keypair:
    def __init__(self, seed: bytes):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        if len(seed) != 32:
            raise ValueError("bad key length")
        self._sk = Ed25519PrivateKey.from_private_bytes(seed)
        self._seed = seed
        self.public = self._sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    @property
    def address(self) -> str:
        return b58encode(self.public)

    def sign(self, msg: bytes) -> bytes:
        return self._sk.sign(msg)

    def __repr__(self):  # never expose the seed
        return f"Keypair({self.address})"

    @classmethod
    def generate(cls):
        return cls(os.urandom(32))

    @classmethod
    def load(cls, path):
        try:
            raw = bytes(orjson.loads(Path(path).read_bytes()))
            kp = cls(raw[:32])
        except (OSError, ValueError, TypeError, orjson.JSONDecodeError):
            raise ValueError(f"cannot read a Solana keypair file at {path}") from None
        if len(raw) != 64 or kp.public != raw[32:]:
            raise ValueError(f"{path} is not a valid Solana keypair file")
        return kp

    def save(self, path):
        p = Path(path)
        if p.exists():
            raise FileExistsError(f"{p} already exists; refusing to overwrite a key")
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "wb") as f:
            f.write(orjson.dumps(list(self._seed + self.public)))


def _cu16(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def memo_transaction(kp: Keypair, memo: bytes, blockhash: str) -> tuple[bytes, str]:
    """Legacy transaction with one Memo-program instruction. Returns (wire bytes, signature)."""
    msg = (bytes([1, 0, 1]) + _cu16(2) + kp.public + b58decode(MEMO_PROGRAM) + b58decode(blockhash)
           + _cu16(1) + bytes([1]) + _cu16(1) + bytes([0]) + _cu16(len(memo)) + memo)
    sig = kp.sign(msg)
    return _cu16(1) + sig + msg, b58encode(sig)


class RpcClient:
    def __init__(self, url, post=None, timeout=15.0):
        self.url, self._post, self.timeout, self._id = url, post, timeout, 0

    def call(self, method, params=None):
        if method in WRITE_METHODS:
            require_devnet(self.url)
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or []}
        try:
            body = self._post(self.url, payload) if self._post else self._http_post(payload)
        except RpcError:
            raise
        except Exception as e:  # keep the text out: URLs can embed API keys
            raise RpcError(f"rpc transport failed: {type(e).__name__}") from None
        if not isinstance(body, dict):
            raise RpcError("rpc returned a non-object response")
        if body.get("error"):
            err = body["error"]
            raise RpcError(f"rpc error on {method}: {str(err.get('message', 'error') if isinstance(err, dict) else 'error')[:120]}")
        if "result" not in body:
            raise RpcError("rpc response had no result")
        return body["result"]

    def _http_post(self, payload):
        import httpx
        import time
        for attempt in range(RATE_LIMIT_TRIES):     # public RPCs answer 429 when called too often: wait and retry
            r = httpx.post(self.url, json=payload, timeout=self.timeout)
            if r.status_code != 429 or attempt == RATE_LIMIT_TRIES - 1:
                break
            time.sleep(min(2 ** attempt, 8))
        if r.status_code != 200:
            raise RpcError(f"rpc answered HTTP {r.status_code}")
        return r.json()


def assert_devnet(rpc: RpcClient) -> None:
    """Second guard, before any write: the cluster itself must report devnet's genesis hash."""
    require_devnet(rpc.url)
    if rpc.call("getGenesisHash") != DEVNET_GENESIS:
        raise MainnetWriteRefused("refusing to write: the RPC's genesis hash is not devnet's")


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()
