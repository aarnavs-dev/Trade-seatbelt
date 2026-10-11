# Signal format v1

A signal is one JSON object, sent as the memo of a Solana Memo-program transaction (devnet) and as one line of
`runs/<id>/feed.jsonl`. **Paper only**: a signal is a suggestion to a follower, never an order.

| key | type | meaning |
|---|---|---|
| `v` | int | always `1` |
| `leader_seq` | int >= 1 | per-leader counter, unique within a feed |
| `symbol` | string | one of AAPL, MSFT, NVDA, TSLA, SPY |
| `side` | string | `BUY` or `SELL` |
| `qty` | int 1..1,000,000 | whole shares |
| `ref_price` | int | cents; the close of the bar that triggered the signal |
| `data_ts` | int | epoch ms UTC, END of the bar used |
| `data_source` | string `[a-z0-9-]{1,16}` | e.g. `alpaca`, `yfinance` |
| `leader_ts` | int | epoch ms UTC decision time (replay: equals `data_ts`) |

Exactly these keys, nothing else. Floats, booleans, extra/missing keys, unknown symbols and memos over 400 bytes are
rejected with a reason. Readers verify that the transaction's **fee payer** is on the signer allowlist.

Ordering: chain feeds sort by `(slot, tx index, signature)`; `tx index` is 0 when the RPC gives none. Local feeds sort by
`leader_seq`. A repeated `leader_seq` from the same signer is rejected.

`feed.jsonl` lines wrap the signal: `{"signal": {...}, "via": "chain"|"local-only", "tx": "<signature>"|null}`, plus an
`{"event":"chain_disabled", ...}` line if devnet publishing stopped. The wrapper is not part of the memo.
