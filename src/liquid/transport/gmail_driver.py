"""Gmail REST transport driver — read, send, and perceive over the Gmail API.

The provider-API counterpart to the raw IMAP/SMTP drivers: instead of sockets it
rides HTTP (``https://gmail.googleapis.com/gmail/v1/users/me``) with the shared
httpx client and the OAuth2 ``httpx.Auth`` the Fetcher already builds from the
adapter's ``auth_scheme`` — so token refresh on 401 is handled by the existing
OAuth2 flow, for free.

- ``fetch`` lists messages (``messages.list``) and hydrates each (``messages.get``,
  ``format=full``) into the same normalized record shape the IMAP driver emits.
- ``write`` (``op="insert"``) composes a MIME message (reusing the SMTP builder)
  and posts it base64url-encoded to ``messages.send``.
- ``sense`` delta-polls ``history.list`` from a ``startHistoryId`` cursor and emits
  each newly-added message as a ``modality="message"`` event.

Requires the OAuth2 ``gmail.modify`` scope. Endpoints carry ``protocol="gmail"``.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
from typing import TYPE_CHECKING, Any

import httpx

from liquid.transport._sql import coerce_limit
from liquid.transport.base import DriverResponse, FetchContext, SenseContext, SenseEvent, WriteContext
from liquid.transport.smtp_driver import build_message

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


class GmailDriver:
    scheme = "gmail"

    async def fetch(self, ctx: FetchContext) -> DriverResponse:
        params = ctx.params or {}
        limit = coerce_limit(params.get("limit"))
        query: dict[str, Any] = {"maxResults": limit}
        if ctx.cursor:
            query["pageToken"] = ctx.cursor
        if params.get("q"):
            query["q"] = params["q"]
        if params.get("labelIds"):
            query["labelIds"] = params["labelIds"]

        async with _client(ctx) as client:
            listing = await client.get(f"{_base(ctx)}/messages", params=query, auth=ctx.auth)
            if not listing.is_success:
                return _error(listing)
            data = listing.json()
            records: list[dict[str, Any]] = []
            for ref in data.get("messages", []):
                msg = await client.get(f"{_base(ctx)}/messages/{ref['id']}", params={"format": "full"}, auth=ctx.auth)
                if msg.is_success:
                    records.append(_gmail_record(msg.json()))
            return DriverResponse(
                status_code=200,
                records=records,
                next_cursor=data.get("nextPageToken"),
                raw=listing,
            )

    async def write(self, ctx: WriteContext) -> DriverResponse:
        if ctx.op != "insert":
            return DriverResponse(status_code=400, error_body=f"unsupported op {ctx.op!r}; send is 'insert'")
        meta = ctx.endpoint.transport_meta or {}
        try:
            msg, _ = build_message(ctx.values or {}, default_from=meta.get("user", ""))
        except ValueError as e:
            return DriverResponse(status_code=400, error_body=str(e))

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        async with _client(ctx) as client:
            resp = await client.post(f"{_base(ctx)}/messages/send", json={"raw": raw}, auth=ctx.auth)
        if not resp.is_success:
            return _error(resp)
        sent = resp.json()
        return DriverResponse(
            status_code=200,
            records=[{"id": sent.get("id"), "thread_id": sent.get("threadId"), "message_id": msg["Message-ID"]}],
            raw=resp,
        )

    async def sense(self, ctx: SenseContext) -> AsyncIterator[SenseEvent]:
        """Delta-poll ``history.list`` for newly-added messages.

        Starts from ``cursor`` (a Gmail ``historyId``); when absent, anchors to the
        mailbox's current ``historyId`` (via the profile) so only mail arriving
        *after* the call is perceived. Each new message is hydrated and emitted as
        a ``modality="message"`` event, bounded by ``max_events`` / ``max_seconds``.
        """
        emitted = 0
        loop = asyncio.get_running_loop()
        deadline = (loop.time() + ctx.max_seconds) if ctx.max_seconds is not None else None

        async with _client(ctx) as client:
            last = ctx.cursor
            if not last:
                prof = await client.get(f"{_base(ctx)}/profile", auth=ctx.auth)
                if not prof.is_success:
                    return
                last = str(prof.json().get("historyId", ""))

            while True:
                try:
                    new_records, last = await _poll_history(client, _base(ctx), ctx.auth, last)
                except Exception:
                    logger.debug("Gmail sense poll failed", exc_info=True)
                    return
                for rec in new_records:
                    yield SenseEvent(source=ctx.endpoint.path, modality="message", payload=rec, cursor=last)
                    emitted += 1
                    if ctx.max_events is not None and emitted >= ctx.max_events:
                        return
                if deadline is not None:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        return
                    await asyncio.sleep(min(ctx.poll_interval, remaining))
                else:
                    await asyncio.sleep(ctx.poll_interval)


async def _poll_history(
    client: httpx.AsyncClient, base: str, auth: httpx.Auth | None, start_history_id: str | None
) -> tuple[list[dict[str, Any]], str | None]:
    """One history.list poll; returns newly-added message records and the new cursor."""
    resp = await client.get(
        f"{base}/history",
        params={"startHistoryId": start_history_id, "historyTypes": "messageAdded"},
        auth=auth,
    )
    if not resp.is_success:
        return [], start_history_id
    data = resp.json()
    new_cursor = str(data.get("historyId", start_history_id))
    ids: list[str] = []
    for entry in data.get("history", []):
        for added in entry.get("messagesAdded", []):
            mid = added.get("message", {}).get("id")
            if mid:
                ids.append(mid)
    records: list[dict[str, Any]] = []
    for mid in ids:
        msg = await client.get(f"{base}/messages/{mid}", params={"format": "full"}, auth=auth)
        if msg.is_success:
            records.append(_gmail_record(msg.json()))
    return records, new_cursor


def _gmail_record(m: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Gmail message resource into the shared mail record shape."""
    payload = m.get("payload", {})
    headers = {h.get("name", "").lower(): h.get("value") for h in payload.get("headers", [])}
    return {
        "id": m.get("id"),
        "thread_id": m.get("threadId"),
        "message_id": headers.get("message-id"),
        "from": headers.get("from"),
        "to": headers.get("to"),
        "cc": headers.get("cc"),
        "subject": headers.get("subject"),
        "date": headers.get("date"),
        "labels": m.get("labelIds", []),
        "snippet": m.get("snippet"),
        "body": _gmail_body(payload) or m.get("snippet", ""),
    }


def _gmail_body(payload: dict[str, Any]) -> str:
    """Best-effort plaintext body — walk MIME parts for the first text/plain."""
    mime = payload.get("mimeType", "")
    body = payload.get("body", {})
    if mime == "text/plain" and body.get("data"):
        return _decode_b64url(body["data"])
    for part in payload.get("parts", []):
        found = _gmail_body(part)
        if found:
            return found
    return ""


def _decode_b64url(data: str) -> str:
    with contextlib.suppress(Exception):
        padded = data + "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(padded).decode("utf-8", "replace")
    return ""


def _base(ctx: FetchContext | WriteContext | SenseContext) -> str:
    base = (ctx.base_url or "").rstrip("/")
    return base if base.startswith("http") else _BASE


def _client(ctx: FetchContext | WriteContext | SenseContext) -> Any:
    """The shared httpx client, or a short-lived one closed after use."""
    if ctx.http_client is not None:
        return contextlib.nullcontext(ctx.http_client)
    return httpx.AsyncClient()


def _error(resp: httpx.Response) -> DriverResponse:
    return DriverResponse(
        status_code=resp.status_code,
        headers=dict(resp.headers),
        error_body=resp.text[:500],
        raw=resp,
    )
