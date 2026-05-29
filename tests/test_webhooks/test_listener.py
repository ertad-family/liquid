"""Inbound webhook listener as a sense — verified end-to-end against a real
localhost server with httpx POSTs (deterministic, no external service)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json

import httpx

from liquid.webhooks import GenericHMACWebhookVerifier, InMemoryIdempotencyStore, WebhookListener

_SECRET = "shh"
_HEADER = "x-signature"


def _verifier() -> GenericHMACWebhookVerifier:
    return GenericHMACWebhookVerifier(_SECRET, header_name=_HEADER)


def _sign(body: bytes) -> str:
    return hmac.new(_SECRET.encode(), body, hashlib.sha256).hexdigest()


async def _wait_for_port(listener: WebhookListener, timeout: float = 2.0) -> int:
    deadline = asyncio.get_running_loop().time() + timeout
    while listener.port == 0:
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError("listener did not bind")
        await asyncio.sleep(0.01)
    return listener.port


async def _collect(listener: WebhookListener, **bounds) -> list:
    events: list = []

    async def consume():
        async for e in listener.events(**bounds):
            events.append(e)

    task = asyncio.create_task(consume())
    return events, task


async def test_verified_webhook_becomes_event():
    listener = WebhookListener(port=0, path="/wh", verifier=_verifier())
    events, task = await _collect(listener, max_events=1, max_seconds=5)
    port = await _wait_for_port(listener)

    body = json.dumps({"id": "evt_1", "type": "order.created", "amount": 9}).encode()
    async with httpx.AsyncClient() as c:
        resp = await c.post(f"http://127.0.0.1:{port}/wh", content=body, headers={_HEADER: _sign(body)})
    await task

    assert resp.status_code == 200
    assert len(events) == 1
    assert events[0].event_type == "order.created"
    assert events[0].payload["amount"] == 9


async def test_bad_signature_is_rejected_and_not_yielded():
    listener = WebhookListener(port=0, path="/wh", verifier=_verifier())
    events, task = await _collect(listener, max_seconds=0.4)
    port = await _wait_for_port(listener)

    body = json.dumps({"id": "evt_2"}).encode()
    async with httpx.AsyncClient() as c:
        resp = await c.post(f"http://127.0.0.1:{port}/wh", content=body, headers={_HEADER: "deadbeef"})
    await task  # ends on max_seconds

    assert resp.status_code == 401
    assert events == []


async def test_wrong_path_returns_404():
    listener = WebhookListener(port=0, path="/wh", verifier=_verifier())
    events, task = await _collect(listener, max_seconds=0.4)
    port = await _wait_for_port(listener)

    body = json.dumps({"id": "evt_3"}).encode()
    async with httpx.AsyncClient() as c:
        resp = await c.post(f"http://127.0.0.1:{port}/nope", content=body, headers={_HEADER: _sign(body)})
    await task

    assert resp.status_code == 404
    assert events == []


async def test_duplicate_event_is_deduped():
    store = InMemoryIdempotencyStore()
    listener = WebhookListener(port=0, path="/wh", verifier=_verifier(), idempotency_store=store)
    # Time-bounded (not event-bounded) so the server stays up for both POSTs.
    events, task = await _collect(listener, max_seconds=0.5)
    port = await _wait_for_port(listener)

    body = json.dumps({"id": "evt_dup", "type": "x"}).encode()
    headers = {_HEADER: _sign(body)}
    async with httpx.AsyncClient() as c:
        first = await c.post(f"http://127.0.0.1:{port}/wh", content=body, headers=headers)
        second = await c.post(f"http://127.0.0.1:{port}/wh", content=body, headers=headers)
    await task  # ends on max_seconds

    assert first.status_code == 200
    assert second.status_code == 200  # acked, but not reprocessed
    assert len(events) == 1  # the duplicate was dropped


async def test_unverified_listener_trusts_and_parses():
    # No verifier → deliveries are trusted and parsed (use only behind a trusted tunnel).
    listener = WebhookListener(port=0, path="/hook")
    events, task = await _collect(listener, max_events=1, max_seconds=5)
    port = await _wait_for_port(listener)

    body = json.dumps({"hello": "world"}).encode()
    async with httpx.AsyncClient() as c:
        resp = await c.post(f"http://127.0.0.1:{port}/hook", content=body)
    await task

    assert resp.status_code == 200
    assert events[0].payload == {"hello": "world"}
