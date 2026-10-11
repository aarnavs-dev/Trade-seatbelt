"""Newline-delimited JSON over TCP. Responses come back in request order per connection."""

import asyncio

import orjson

from gate.engine import Gate, GateClosed

MAX_LINE = 4096


def _handler(gate: Gate):
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        pending: asyncio.Queue = asyncio.Queue()

        async def respond():
            """Write every already-completed response in one socket call (order preserved)."""
            chunks = []

            async def result(fut):
                try:
                    return await fut
                except GateClosed as e:
                    return orjson.dumps({"error": str(e)}) + b"\n"

            while (fut := await pending.get()) is not None:
                chunks.append(await result(fut))
                while not pending.empty() and len(chunks) < 512:
                    nxt = pending.get_nowait()
                    if nxt is None:
                        pending.put_nowait(None)  # leave the end marker for the outer loop
                        break
                    if not nxt.done():  # flush what is ready first, then wait for this one
                        writer.write(b"".join(chunks))
                        chunks = []
                    chunks.append(await result(nxt))
                writer.write(b"".join(chunks))
                chunks = []
                await writer.drain()

        task = asyncio.create_task(respond())
        try:
            while True:
                try:
                    raw = await reader.readline()
                except ValueError:  # line longer than MAX_LINE
                    writer.write(orjson.dumps({"error": "line too long"}) + b"\n")
                    break
                if not raw:
                    break
                pending.put_nowait(await gate.submit(raw))
        except (GateClosed, ConnectionError):
            pass
        finally:
            pending.put_nowait(None)
            try:
                await task
                writer.close()
            except (ConnectionError, GateClosed):
                pass

    return handle


async def start_server(gate: Gate, host: str, port: int) -> asyncio.Server:
    return await asyncio.start_server(_handler(gate), host, port, limit=MAX_LINE)
