"""Gmail driver: list/get fetch, send, history sense, discovery (Phase 3).

Drives the driver against an httpx.MockTransport standing in for the Gmail API —
no network, no credentials.
"""

from __future__ import annotations

import base64
import json

import httpx

from liquid.discovery.email import EmailDiscovery
from liquid.models.schema import Endpoint, EndpointKind
from liquid.transport.base import FetchContext, SenseContext, WriteContext
from liquid.transport.gmail_driver import GmailDriver

_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def _full_message(mid: str) -> dict:
    return {
        "id": mid,
        "threadId": f"t{mid}",
        "labelIds": ["INBOX"],
        "snippet": "hi there",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "a@x.com"},
                {"name": "To", "value": "me@gmail.com"},
                {"name": "Subject", "value": "Hello"},
                {"name": "Date", "value": "Tue, 03 Jun 2026 10:00:00 +0000"},
                {"name": "Message-ID", "value": f"<{mid}@x>"},
            ],
            "body": {"data": _b64url("the real body")},
        },
    }


def _handler(captured: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if captured is not None:
            captured[request.method + " " + path] = request
        if path.endswith("/messages") and request.method == "GET":
            return httpx.Response(200, json={"messages": [{"id": "1"}], "nextPageToken": "NP"})
        if path.endswith("/messages/send"):
            return httpx.Response(200, json={"id": "s1", "threadId": "t1"})
        if path.endswith("/profile"):
            return httpx.Response(200, json={"historyId": "100"})
        if path.endswith("/history"):
            return httpx.Response(
                200,
                json={"history": [{"messagesAdded": [{"message": {"id": "2"}}]}], "historyId": "101"},
            )
        if "/messages/" in path:
            mid = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json=_full_message(mid))
        return httpx.Response(404, json={"error": "not found"})

    return handler


def _client(captured: dict | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(_handler(captured)))


def _read_ep() -> Endpoint:
    return Endpoint(path="/messages", protocol="gmail", transport_meta={"kind": "messages", "user": "me@gmail.com"})


# --- fetch ----------------------------------------------------------------


async def test_gmail_fetch_lists_and_hydrates():
    async with _client() as client:
        ctx = FetchContext(
            endpoint=_read_ep(),
            base_url=_BASE,
            params={"limit": 10},
            headers={},
            cursor=None,
            selector=None,  # type: ignore[arg-type]
            pagination=None,  # type: ignore[arg-type]
            vault=None,  # type: ignore[arg-type]
            auth_ref="k",
            http_client=client,
        )
        resp = await GmailDriver().fetch(ctx)
    assert resp.status_code == 200
    assert resp.next_cursor == "NP"
    assert len(resp.records) == 1
    rec = resp.records[0]
    assert rec["from"] == "a@x.com"
    assert rec["subject"] == "Hello"
    assert rec["id"] == "1"
    assert rec["body"] == "the real body"


# --- send -----------------------------------------------------------------


async def test_gmail_send():
    captured: dict = {}
    async with _client(captured) as client:
        ctx = WriteContext(
            endpoint=Endpoint(
                path="/messages/send",
                method="POST",
                protocol="gmail",
                kind=EndpointKind.WRITE,
                transport_meta={"kind": "send", "user": "me@gmail.com"},
            ),
            base_url=_BASE,
            op="insert",
            values={"to": "a@x.com", "subject": "hi", "body": "yo"},
            where={},
            vault=None,  # type: ignore[arg-type]
            auth_ref="k",
            http_client=client,
        )
        resp = await GmailDriver().write(ctx)
    assert resp.status_code == 200
    assert resp.records[0]["id"] == "s1"
    assert resp.records[0]["message_id"]
    # the posted body is a base64url MIME containing the recipient
    sent = json.loads(captured["POST /gmail/v1/users/me/messages/send"].content)
    raw = base64.urlsafe_b64decode(sent["raw"] + "=" * (-len(sent["raw"]) % 4)).decode()
    assert "a@x.com" in raw and "Subject: hi" in raw


async def test_gmail_send_rejects_non_insert():
    ctx = WriteContext(
        endpoint=_read_ep(),
        base_url=_BASE,
        op="update",
        values={},
        where={},
        vault=None,  # type: ignore[arg-type]
        auth_ref="k",
    )
    resp = await GmailDriver().write(ctx)
    assert resp.status_code == 400


# --- sense ----------------------------------------------------------------


async def test_gmail_sense_history_poll():
    async with _client() as client:
        ctx = SenseContext(
            endpoint=_read_ep(),
            base_url=_BASE,
            params={},
            vault=None,  # type: ignore[arg-type]
            auth_ref="k",
            cursor=None,
            poll_interval=0.0,
            max_events=1,
            http_client=client,
        )
        events = [ev async for ev in GmailDriver().sense(ctx)]
    assert len(events) == 1
    ev = events[0]
    assert ev.modality == "message"
    assert ev.payload["id"] == "2"
    assert ev.cursor == "101"


# --- discovery ------------------------------------------------------------


async def test_gmail_discovery_shape():
    schema = await EmailDiscovery().discover("gmail://me@gmail.com")
    assert schema is not None
    assert schema.discovery_method == "email"
    assert schema.auth.type == "oauth2" and schema.auth.tier == "A"
    paths = {(e.method, e.path, e.protocol) for e in schema.endpoints}
    assert ("GET", "/messages", "gmail") in paths
    assert ("POST", "/messages/send", "gmail") in paths
