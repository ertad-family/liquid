"""IMAP transport driver — read a mailbox as records, perceive new mail as events.

A mailbox isn't tabular: messages are addressed by a monotonic per-folder ``UID``.
The driver ``UID SEARCH``es the folder (selected from ``transport_meta["mailbox"]``,
default ``INBOX``) and ``FETCH``es each message, yielding a normalized
``{uid, from, to, subject, date, body, flags, …}`` record. Pagination is native
cursor-based: the fetch cursor *is* the last-seen UID, surfaced via ``next_cursor``
(messages with ``UID > cursor``), like Redis SCAN rather than offset paging.

``sense`` is the afferent counterpart — a delta-poll that re-runs the same
``UID > cursor`` search on an interval and emits each new message as a
``modality="message"`` event, bounded by ``max_events`` / ``max_seconds``.

Connection is an ``imap://`` / ``imaps://`` DSN; the persisted URL is
credential-redacted and the password is pulled from the vault at call time. Built
on the stdlib :mod:`imaplib`, run inside :func:`asyncio.to_thread` so the blocking
socket calls never stall the event loop — no third-party dependency for basic /
app-password auth.
"""

from __future__ import annotations

import asyncio
import contextlib
import imaplib
import logging
from email import message_from_bytes
from email.policy import default as _email_policy
from typing import TYPE_CHECKING, Any

from liquid.transport._mail import IMAP_SCHEMES, MailDSN, resolve_mail_dsn
from liquid.transport._sql import coerce_limit
from liquid.transport.base import DriverResponse, FetchContext, SenseContext, SenseEvent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from email.message import EmailMessage

logger = logging.getLogger(__name__)


class IMAPDriver:
    scheme = "imap"

    async def fetch(self, ctx: FetchContext) -> DriverResponse:
        try:
            dsn = await resolve_mail_dsn(ctx, IMAP_SCHEMES)
        except Exception:
            return DriverResponse(status_code=503, error_body="no IMAP DSN")

        meta = ctx.endpoint.transport_meta or {}
        params = ctx.params or {}
        mailbox = params.get("mailbox") or meta.get("mailbox") or dsn.mailbox
        limit = coerce_limit(params.get("limit"))
        since_uid = _coerce_uid_cursor(ctx.cursor)

        try:
            records, last_uid = await asyncio.to_thread(_search_fetch_sync, dsn, mailbox, since_uid, limit)
        except Exception as e:
            return _map_imap_error(e)

        next_cursor = str(last_uid) if last_uid is not None else None
        return DriverResponse(status_code=200, records=records, next_cursor=next_cursor)

    async def sense(self, ctx: SenseContext) -> AsyncIterator[SenseEvent]:
        """Delta-poll the mailbox for new mail (``UID > cursor``).

        Re-runs the UID search every ``poll_interval`` seconds, emitting each new
        message as a ``modality="message"`` event whose ``cursor`` is its UID, so
        a consumer resumes exactly where it left off. Bounded by ``max_events`` /
        ``max_seconds`` so it never blocks forever.
        """
        try:
            dsn = await resolve_mail_dsn(ctx, IMAP_SCHEMES)
        except Exception:
            return
        meta = ctx.endpoint.transport_meta or {}
        mailbox = (ctx.params or {}).get("mailbox") or meta.get("mailbox") or dsn.mailbox
        last = _coerce_uid_cursor(ctx.cursor)

        emitted = 0
        loop = asyncio.get_running_loop()
        deadline = (loop.time() + ctx.max_seconds) if ctx.max_seconds is not None else None
        while True:
            try:
                records, _ = await asyncio.to_thread(_search_fetch_sync, dsn, mailbox, last, _SENSE_BATCH)
            except Exception:
                logger.debug("IMAP sense poll failed", exc_info=True)
                return
            for rec in records:
                yield SenseEvent(
                    source=ctx.endpoint.path,
                    modality="message",
                    payload=rec,
                    cursor=rec["uid"],
                )
                last = int(rec["uid"])
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


_SENSE_BATCH = 50


def _coerce_uid_cursor(cursor: str | None) -> int:
    try:
        return max(0, int(cursor))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


# --- blocking imaplib core (runs in a worker thread) ----------------------


def _connect(dsn: MailDSN) -> imaplib.IMAP4:
    if dsn.use_ssl:
        client: imaplib.IMAP4 = imaplib.IMAP4_SSL(dsn.host, dsn.port)
    else:
        client = imaplib.IMAP4(dsn.host, dsn.port)
        if dsn.use_starttls:
            client.starttls()
    client.login(dsn.username, dsn.password)
    return client


def _search_fetch_sync(
    dsn: MailDSN, mailbox: str, since_uid: int, limit: int
) -> tuple[list[dict[str, Any]], int | None]:
    """Select ``mailbox``, fetch up to ``limit`` messages with ``UID > since_uid``.

    Returns the records (oldest-first) and the highest UID seen (the next cursor),
    or ``None`` when nothing new arrived.
    """
    client = _connect(dsn)
    try:
        client.select(mailbox, readonly=True)
        # `UID n:*` always returns at least the highest UID even when none exceed
        # n, so filter explicitly; "1:*" (since_uid 0) means "everything".
        criterion = f"UID {since_uid + 1}:*" if since_uid else "ALL"
        typ, data = client.uid("search", None, criterion)
        if typ != "OK" or not data or not data[0]:
            return [], None
        uids = sorted({int(u) for u in data[0].split() if int(u) > since_uid})
        batch = uids[:limit]
        if not batch:
            return [], None
        records: list[dict[str, Any]] = []
        for uid in batch:
            typ, msgdata = client.uid("fetch", str(uid), "(BODY.PEEK[] FLAGS)")
            if typ != "OK" or not msgdata:
                continue
            rec = _parse_fetch_item(uid, msgdata)
            if rec is not None:
                records.append(rec)
        return records, batch[-1]
    finally:
        with contextlib.suppress(Exception):
            client.logout()


def _parse_fetch_item(uid: int, msgdata: list[Any]) -> dict[str, Any] | None:
    """Turn one imaplib FETCH response into a record.

    A FETCH reply is a list whose payload element is a ``(info, raw)`` tuple —
    ``info`` carries the flags, ``raw`` the full RFC822 message bytes.
    """
    info = b""
    raw: bytes | None = None
    for part in msgdata:
        if isinstance(part, tuple) and len(part) == 2:
            info, raw = part[0] or b"", part[1]
    if raw is None:
        return None
    try:
        flags = [f.decode() if isinstance(f, bytes) else str(f) for f in imaplib.ParseFlags(info)]
    except Exception:
        flags = []
    msg = message_from_bytes(raw, policy=_email_policy)
    return message_to_record(uid, flags, msg)  # type: ignore[arg-type]


def message_to_record(uid: int, flags: list[str], msg: EmailMessage) -> dict[str, Any]:
    """Normalize a parsed email into a flat, JSON-friendly record."""
    return {
        "uid": str(uid),
        "message_id": _header(msg, "Message-ID"),
        "from": _header(msg, "From"),
        "to": _header(msg, "To"),
        "cc": _header(msg, "Cc"),
        "subject": _header(msg, "Subject"),
        "date": _header(msg, "Date"),
        "flags": flags,
        "body": _extract_body(msg),
    }


def _header(msg: EmailMessage, name: str) -> str | None:
    value = msg.get(name)
    return str(value) if value is not None else None


def _extract_body(msg: EmailMessage) -> str:
    """Best-effort plaintext body (prefers text/plain, falls back to text/html)."""
    with contextlib.suppress(Exception):
        part = msg.get_body(preferencelist=("plain", "html"))
        if part is not None:
            content = part.get_content()
            return content if isinstance(content, str) else str(content)
    with contextlib.suppress(Exception):
        if not msg.is_multipart():
            content = msg.get_content()
            return content if isinstance(content, str) else str(content)
    return ""


def _map_imap_error(e: Exception) -> DriverResponse:
    detail = str(e)[:500]
    if isinstance(e, imaplib.IMAP4.error):
        low = detail.lower()
        if any(tok in low for tok in ("auth", "credential", "login", "password")):
            return DriverResponse(status_code=401, error_body=detail)
        return DriverResponse(status_code=400, error_body=detail)
    if isinstance(e, OSError | TimeoutError):
        return DriverResponse(status_code=503, error_body=detail)
    return DriverResponse(status_code=503, error_body=detail)
