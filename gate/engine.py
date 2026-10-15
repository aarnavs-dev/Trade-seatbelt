"""Single-writer engine. One task owns the Book and the log; everything else only enqueues."""

import asyncio
import time

import orjson
from pydantic import ValidationError

from core.events import Event, Order
from core.book import Book
from core.logio import LogWriter, event_line, repair_torn_tail, scan_records
from core.rules import rules_hash


class GateClosed(Exception):
    pass


def _resp(ev: Event, duplicate: bool) -> bytes:
    return orjson.dumps({
        "seq": ev.seq, "client_order_id": ev.client_order_id, "decision": ev.decision,
        "reason_code": ev.reason_code, "reason_text": ev.reason_text, "hash": ev.hash,
        "duplicate": duplicate,
    }) + b"\n"


def _err(msg: str) -> bytes:
    return orjson.dumps({"error": msg}) + b"\n"


class Gate:
    def __init__(self, rules, log_path, fsync="batched", batch_max=256, clock=time.time_ns,
                 queue_size=10_000, full_fsync=False, record_rules=True):
        self.rules, self.log_path, self.clock = rules, log_path, clock
        self.fsync, self.full_fsync = fsync, full_fsync
        # per_order means exactly one event per fsync; batched is group commit of whatever is queued
        self.batch_max = 1 if fsync == "per_order" else batch_max
        self.queue_size = queue_size
        self.book = Book(rules)
        self.closed = True
        self.recovery_note = None
        self.record_rules = record_rules  # False only to write a v1-style log (compatibility fixtures and tests)
        self.replayed = 0                 # order events read back at start-up (control records are not counted)
        self.rules_record = None          # the RulesRecord this start appended, or None if the log already had these rules

    async def start(self) -> None:
        self.recovery_note = repair_torn_tail(self.log_path)  # only a partial last line is repaired
        for rec in scan_records(self.log_path):  # rebuild state; refuses to start on a broken chain
            if isinstance(rec, Event):
                self.book.apply(rec)
                self.replayed += 1
            else:
                self.book.apply_record(rec)
        self.writer = LogWriter(self.log_path, self.fsync, self.full_fsync)
        if self.record_rules and self.book.recorded_rules_hash != rules_hash(self.rules):
            # Rules are loaded once per process (there is no live reload), so one record per start whose rules differ from
            # the last recorded ones pins every later order to them. Durable before the first order is accepted.
            rec = self.book.make_rules_record(max(self.clock(), self.book.last_ts))
            self.writer.write(event_line(rec))
            self.book.apply_record(rec)
            self.rules_record = rec
        self.queue: asyncio.Queue = asyncio.Queue(self.queue_size)
        self.closed = False
        self.task = asyncio.create_task(self._run())

    async def submit(self, raw: bytes) -> asyncio.Future:
        """Enqueue one request line. The future resolves to the response line after the event is
        durable according to the fsync policy."""
        if self.closed:
            raise GateClosed("gate is not accepting orders")
        fut = asyncio.get_running_loop().create_future()
        await self.queue.put((raw, fut))
        return fut

    async def stop(self) -> None:
        if self.closed:
            return
        self.closed = True
        await self.queue.put(None)
        await self.task
        self.writer.close()

    def _handle(self, raw: bytes):
        """Returns (event_or_None, immediate_response_or_None). Applies the event to the Book."""
        try:
            order = Order.model_validate_json(raw)
        except ValidationError as e:
            first = e.errors()[0]
            return None, _err(f"invalid order: {'.'.join(map(str, first['loc']))}: {first['msg']}")
        prior = self.book.seen.get(order.client_order_id)
        if prior is not None:  # idempotent: original decision, nothing logged or counted
            return None, _resp(prior, True)
        ts = max(self.clock(), self.book.last_ts)
        ev = self.book.make_event(order, ts, self.book.verdict(order, ts))
        self.book.apply(ev)
        return ev, None

    async def _run(self) -> None:
        q, stop = self.queue, False
        while not stop:
            item = await q.get()
            if item is None:
                break
            batch = [item]
            while len(batch) < self.batch_max:
                try:
                    nxt = q.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if nxt is None:
                    stop = True
                    break
                batch.append(nxt)
            self._commit(batch)

    def _commit(self, batch) -> None:
        outs, lines = [], []
        for raw, fut in batch:
            ev, resp = self._handle(raw)
            if ev is not None:
                lines.append(event_line(ev))
                resp = _resp(ev, False)
            outs.append((fut, resp))
        try:
            if lines:
                self.writer.write(b"".join(lines))
        except OSError as e:  # state is now ahead of the log: stop rather than diverge
            self.closed = True
            for fut, _ in outs:
                fut.set_exception(GateClosed(f"log write failed: {e}"))
            return
        for fut, resp in outs:
            fut.set_result(resp)
