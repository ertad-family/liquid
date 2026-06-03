"""Email discovery — turn a mail DSN into read/sense and send endpoints.

An ``imap://`` URL is introspected by ``LIST``-ing its mailboxes: each folder
becomes a read endpoint (``protocol="imap"``) that is also sense-capable, so the
agent gets ``list_<folder>`` and ``sense_<folder>`` tools for free. An ``smtp://``
URL has nothing to introspect — it yields a single write endpoint
(``protocol="smtp"``, ``/outbox``) for sending. Any other URL returns ``None``.

IMAP introspection runs on the stdlib :mod:`imaplib` inside a worker thread; no
third-party dependency is needed for password / app-password auth.
"""

from __future__ import annotations

import asyncio
import contextlib
import imaplib
import logging
from urllib.parse import unquote, urlsplit

from liquid.exceptions import DiscoveryError, Recovery
from liquid.models.schema import APISchema, AuthRequirement, Endpoint, EndpointKind
from liquid.transport._mail import IMAP_SCHEMES, SMTP_SCHEMES, parse_imap_dsn
from liquid.transport._sql import is_dsn, redact_dsn

logger = logging.getLogger(__name__)

_MESSAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "uid": {"type": "string"},
        "message_id": {"type": "string"},
        "from": {"type": "string"},
        "to": {"type": "string"},
        "cc": {"type": "string"},
        "subject": {"type": "string"},
        "date": {"type": "string"},
        "flags": {"type": "array", "items": {"type": "string"}},
        "body": {"type": "string"},
    },
}

_SEND_SCHEMA = {
    "type": "object",
    "properties": {
        "to": {"type": "string", "description": "Recipient(s), comma-separated or a list."},
        "subject": {"type": "string"},
        "body": {"type": "string", "description": "Plaintext body."},
        "cc": {"type": "string"},
        "bcc": {"type": "string"},
        "html": {"type": "string", "description": "Optional HTML alternative body."},
        "from": {"type": "string", "description": "Sender; defaults to the DSN user."},
        "reply_to": {"type": "string"},
    },
    "required": ["to"],
}


_GMAIL_SCHEMES = ("gmail://",)
_GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"


class EmailDiscovery:
    async def discover(self, url: str) -> APISchema | None:
        if is_dsn(url, _GMAIL_SCHEMES):
            return self._discover_gmail(url)
        if is_dsn(url, IMAP_SCHEMES):
            return await self._discover_imap(url)
        if is_dsn(url, SMTP_SCHEMES):
            return self._discover_smtp(url)
        return None

    def _discover_gmail(self, url: str) -> APISchema:
        # Provider API: read/send/sense over HTTP + OAuth2. Nothing to introspect
        # live (no token at discovery time) — synthesize the standard endpoints.
        user = _gmail_user(url)
        meta = {"user": user}
        endpoints = [
            Endpoint(
                path="/messages",
                method="GET",
                protocol="gmail",
                kind=EndpointKind.READ,
                description="Gmail messages — list/read and perceive newly-arrived mail.",
                response_schema=_MESSAGE_SCHEMA,
                transport_meta={"kind": "messages", **meta},
            ),
            Endpoint(
                path="/messages/send",
                method="POST",
                protocol="gmail",
                kind=EndpointKind.WRITE,
                description="Send an email via the Gmail API.",
                request_schema=_SEND_SCHEMA,
                transport_meta={"kind": "send", **meta},
            ),
        ]
        return APISchema(
            source_url=_GMAIL_API,
            service_name=f"gmail-{user}" if user else "gmail",
            discovery_method="email",
            endpoints=endpoints,
            auth=AuthRequirement(type="oauth2", tier="A"),
        )

    async def _discover_imap(self, url: str) -> APISchema:
        dsn = parse_imap_dsn(url)
        try:
            mailboxes = await asyncio.to_thread(
                _list_mailboxes, dsn.host, dsn.port, dsn.username, dsn.password, dsn.use_ssl
            )
        except Exception as e:
            raise DiscoveryError(
                f"Could not list IMAP mailboxes: {e}",
                recovery=Recovery(hint="Check the host, port, and credentials (use an app password).", retry_safe=True),
            ) from e

        endpoints = [_mailbox_endpoint(name) for name in mailboxes] or [_mailbox_endpoint("INBOX")]
        return APISchema(
            source_url=redact_dsn(url),
            service_name=_service_name(url, "imap"),
            discovery_method="email",
            endpoints=endpoints,
            auth=AuthRequirement(type="basic", tier="C"),
        )

    def _discover_smtp(self, url: str) -> APISchema:
        # SMTP has no schema to introspect — synthesize the single send endpoint.
        return APISchema(
            source_url=redact_dsn(url),
            service_name=_service_name(url, "smtp"),
            discovery_method="email",
            endpoints=[_outbox_endpoint()],
            auth=AuthRequirement(type="basic", tier="C"),
        )


def _list_mailboxes(host: str, port: int, user: str, password: str, use_ssl: bool) -> list[str]:
    client: imaplib.IMAP4
    if use_ssl:
        client = imaplib.IMAP4_SSL(host, port)
    else:
        client = imaplib.IMAP4(host, port)
        client.starttls()
    try:
        client.login(user, password)
        typ, data = client.list()
        if typ != "OK" or not data:
            return []
        names = []
        for line in data:
            name = _parse_mailbox_line(line)
            if name:
                names.append(name)
        return names
    finally:
        with contextlib.suppress(Exception):
            client.logout()


def _parse_mailbox_line(line: bytes | str | None) -> str | None:
    """Extract the mailbox name from an IMAP LIST reply line.

    Lines look like ``(\\HasNoChildren) "/" "INBOX"`` — the name is the last
    token, quoted when it contains spaces.
    """
    if line is None:
        return None
    text = line.decode("utf-8", "replace") if isinstance(line, bytes) else str(line)
    text = text.strip()
    if not text:
        return None
    if text.endswith('"'):
        start = text.rfind('"', 0, len(text) - 1)
        if start != -1:
            return text[start + 1 : -1]
    return text.rsplit(" ", 1)[-1] or None


def _mailbox_endpoint(name: str) -> Endpoint:
    return Endpoint(
        path=f"/{name}",
        method="GET",
        protocol="imap",
        kind=EndpointKind.READ,
        description=f"IMAP mailbox {name} — read messages and perceive new mail.",
        response_schema=_MESSAGE_SCHEMA,
        transport_meta={"kind": "mailbox", "mailbox": name},
    )


def _outbox_endpoint() -> Endpoint:
    return Endpoint(
        path="/outbox",
        method="POST",
        protocol="smtp",
        kind=EndpointKind.WRITE,
        description="Send an email via SMTP.",
        request_schema=_SEND_SCHEMA,
        transport_meta={"kind": "outbox"},
    )


def _service_name(url: str, kind: str) -> str:
    host = urlsplit(url).hostname or kind
    return f"{kind}-{host}"


def _gmail_user(url: str) -> str:
    """Best-effort email from a ``gmail://`` DSN (``gmail://me@gmail.com`` etc.)."""
    parts = urlsplit(url)
    user = unquote(parts.username or "")
    host = parts.hostname or ""
    if user and host:
        return f"{user}@{host}"
    return user or host
