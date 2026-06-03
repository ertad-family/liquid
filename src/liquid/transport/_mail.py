"""Shared helpers for the mail transport drivers (IMAP read/sense, SMTP send).

Mail isn't tabular and doesn't ride HTTP: a connection is a ``imap://`` / ``smtp://``
DSN (credentials in userinfo, credential-redacted when persisted), resolved at call
time from the vault exactly like the SQL/Redis drivers. This module centralises the
DSN parsing and TLS-mode inference both drivers (and discovery) share, so the wire
semantics live in one place.

Raw IMAP/SMTP run on Python's stdlib ``imaplib`` / ``smtplib``; the drivers call them
inside :func:`asyncio.to_thread` so the blocking socket work never stalls the event
loop. No third-party dependency is required for password / app-password auth.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

from liquid.transport._sql import resolve_dsn

if TYPE_CHECKING:
    from collections.abc import Sequence

    from liquid.transport.base import FetchContext

IMAP_SCHEMES = ("imap://", "imaps://")
SMTP_SCHEMES = ("smtp://", "smtps://")

_DEFAULT_IMAP_PORT = 993
_DEFAULT_SMTP_PORT = 587


@dataclass(slots=True)
class MailDSN:
    """A parsed mail connection: host, port, credentials and resolved TLS mode."""

    host: str
    port: int
    username: str
    password: str
    use_ssl: bool  # implicit TLS on connect (IMAPS / SMTPS)
    use_starttls: bool  # upgrade a plaintext connection with STARTTLS
    mailbox: str = "INBOX"


def parse_imap_dsn(url: str) -> MailDSN:
    """Parse an ``imap://`` / ``imaps://`` DSN.

    Implicit TLS (IMAPS) is assumed unless the connection is to the cleartext
    port 143, where STARTTLS is used instead — matching how IMAP is deployed in
    practice (993 = TLS, 143 = STARTTLS).
    """
    parts = urlsplit(url)
    port = parts.port or _DEFAULT_IMAP_PORT
    use_ssl = parts.scheme == "imaps" or port == _DEFAULT_IMAP_PORT
    mailbox = parts.path.strip("/") or "INBOX"
    return MailDSN(
        host=parts.hostname or "",
        port=port,
        username=unquote(parts.username or ""),
        password=unquote(parts.password or ""),
        use_ssl=use_ssl,
        use_starttls=not use_ssl,
        mailbox=mailbox,
    )


def parse_smtp_dsn(url: str) -> MailDSN:
    """Parse an ``smtp://`` / ``smtps://`` DSN.

    Implicit TLS (SMTPS) on port 465; STARTTLS on the submission port 587 (and
    the default). Plain 25 still negotiates STARTTLS when the server offers it.
    """
    parts = urlsplit(url)
    port = parts.port or _DEFAULT_SMTP_PORT
    use_ssl = parts.scheme == "smtps" or port == 465
    return MailDSN(
        host=parts.hostname or "",
        port=port,
        username=unquote(parts.username or ""),
        password=unquote(parts.password or ""),
        use_ssl=use_ssl,
        use_starttls=not use_ssl,
    )


async def resolve_mail_dsn(ctx: FetchContext, schemes: Sequence[str]) -> MailDSN:
    """Resolve the connection string from the vault, then parse it.

    Works for every context type (fetch / sense / write) since
    :func:`resolve_dsn` only reads ``vault`` / ``auth_ref`` / ``base_url``.
    """
    raw = await resolve_dsn(ctx, schemes)
    return parse_imap_dsn(raw) if schemes is IMAP_SCHEMES else parse_smtp_dsn(raw)
