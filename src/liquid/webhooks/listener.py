"""Inbound webhook listener — perceive webhooks as a sense.

Most senses are *outbound* (the agent connects out and reads); a webhook is the
inverse — the world POSTs *to* the agent. This listener hosts a tiny HTTP
endpoint, verifies each delivery with a :class:`~liquid.webhooks.WebhookVerifier`
(and optionally de-duplicates via an :class:`~liquid.webhooks.IdempotencyStore`),
and yields the verified events as an async stream — the afferent organ pointed at
the network, so "a service (or a human via a webhook) notified me" becomes a
perceivable signal alongside DB deltas and pub/sub.

The server is built on ``asyncio.start_server`` with a minimal HTTP/1.1 parse
(request line + headers + ``Content-Length`` body) so the core stays
dependency-free — same stdlib-first stance as the SQLite/SOAP drivers. It accepts
``POST`` to the configured path; everything else gets a terse status. Unverified
deliveries (bad signature) answer ``401`` and are dropped; duplicates answer
``200`` and are dropped; verified events answer ``200`` and are yielded.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any

from liquid.webhooks.models import (
    DuplicateEventError,
    WebhookEvent,
    WebhookVerificationError,
)
from liquid.webhooks.verifier import verify_webhook

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from liquid.webhooks.idempotency import IdempotencyStore
    from liquid.webhooks.verifier import WebhookVerifier

logger = logging.getLogger(__name__)

_REASONS = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found", 405: "Method Not Allowed"}


class WebhookListener:
    """Serves an inbound webhook endpoint and streams verified events.

    ``port=0`` binds an ephemeral port (read back from :attr:`port` once
    :meth:`events` has started). A ``verifier`` is strongly recommended — without
    one, deliveries are trusted and parsed as JSON (use only behind a trusted
    tunnel). ``idempotency_store`` drops replays. Each verified delivery is
    surfaced as a :class:`~liquid.webhooks.WebhookEvent`.
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        path: str = "/webhook",
        verifier: WebhookVerifier | None = None,
        idempotency_store: IdempotencyStore | None = None,
        ok_status: int = 200,
    ) -> None:
        self._host = host
        self.port = port
        self._path = path if path.startswith("/") else f"/{path}"
        self._verifier = verifier
        self._idempotency_store = idempotency_store
        self._ok_status = ok_status

    async def events(
        self, *, max_events: int | None = None, max_seconds: float | None = None
    ) -> AsyncIterator[WebhookEvent]:
        """Start the server and yield each verified webhook event.

        Bounded by ``max_events`` / ``max_seconds`` (``None`` = unbounded). The
        server is torn down when the iterator finishes.
        """
        queue: asyncio.Queue[WebhookEvent] = asyncio.Queue()
        server = await asyncio.start_server(self._make_handler(queue), self._host, self.port)
        self.port = server.sockets[0].getsockname()[1] if server.sockets else self.port
        logger.info("Webhook listener on http://%s:%s%s", self._host, self.port, self._path)

        emitted = 0
        loop = asyncio.get_running_loop()
        deadline = (loop.time() + max_seconds) if max_seconds is not None else None
        try:
            while True:
                timeout = None
                if deadline is not None:
                    timeout = max(0.0, deadline - loop.time())
                    if timeout == 0.0:
                        return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=timeout)
                except TimeoutError:
                    return
                yield event
                emitted += 1
                if max_events is not None and emitted >= max_events:
                    return
        finally:
            server.close()
            with contextlib.suppress(Exception):
                await server.wait_closed()

    def _make_handler(self, queue: asyncio.Queue[WebhookEvent]) -> Any:
        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                method, target, headers, body = await _read_request(reader)
                status = await self._process(method, target, headers, body, queue)
            except Exception:
                status = 400
            with contextlib.suppress(Exception):
                await _write_response(writer, status)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

        return handle

    async def _process(
        self,
        method: str,
        target: str,
        headers: dict[str, str],
        body: bytes,
        queue: asyncio.Queue[WebhookEvent],
    ) -> int:
        if method.upper() != "POST":
            return 405
        if target.split("?", 1)[0] != self._path:
            return 404

        if self._verifier is not None:
            try:
                event = await verify_webhook(body, headers, self._verifier, idempotency_store=self._idempotency_store)
            except DuplicateEventError:
                return self._ok_status  # already processed — ack without re-yielding
            except WebhookVerificationError:
                return 401
        else:
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return 400
            if not isinstance(payload, dict):
                return 400
            event = WebhookEvent(payload=payload, raw_body=body, provider="generic")

        queue.put_nowait(event)
        return self._ok_status


async def _read_request(reader: asyncio.StreamReader) -> tuple[str, str, dict[str, str], bytes]:
    """Parse a minimal HTTP/1.1 request: request line, headers, Content-Length body."""
    request_line = await reader.readline()
    parts = request_line.decode("latin-1").split()
    if len(parts) < 2:
        raise ValueError("malformed request line")
    method, target = parts[0], parts[1]

    headers: dict[str, str] = {}
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b"\n", b""):
            break
        key, _, value = line.decode("latin-1").partition(":")
        headers[key.strip().lower()] = value.strip()

    length = 0
    with contextlib.suppress(ValueError, TypeError):
        length = int(headers.get("content-length", "0") or 0)
    body = await reader.readexactly(length) if length > 0 else b""
    return method, target, headers, body


async def _write_response(writer: asyncio.StreamWriter, status: int) -> None:
    reason = _REASONS.get(status, "OK")
    body = reason.encode()
    head = (
        f"HTTP/1.1 {status} {reason}\r\n"
        f"Content-Type: text/plain\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: close\r\n\r\n"
    )
    writer.write(head.encode("latin-1") + body)
    await writer.drain()
