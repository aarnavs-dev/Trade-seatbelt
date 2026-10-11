"""Canonical JSON and the hash chain. hash = sha256(prev_hash || canonical JSON of the rest)."""

import hashlib

import orjson

GENESIS = "0" * 64


def canon(obj) -> bytes:
    """Sorted keys, no whitespace. Callers only pass ints/strs/bools (models are strict)."""
    return orjson.dumps(obj, option=orjson.OPT_SORT_KEYS)


def event_hash(prev_hash: str, body: dict) -> str:
    return hashlib.sha256(prev_hash.encode() + canon(body)).hexdigest()
