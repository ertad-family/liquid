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

from liquid.auth.oauth2 import OAuth2TokenProvider
from liquid.transport._sql import is_dsn, resolve_dsn

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


@dataclass(slots=True)
class MailAuth:
    """How to authenticate a mail socket: a password, or an OAuth2 bearer.

    ``mode`` is ``"basic"`` (app-password / login) or ``"xoauth2"`` (SASL
    ``XOAUTH2`` with a Bearer access token). For ``xoauth2`` the ``provider`` lets
    the driver refresh the token once on an auth failure and retry, mirroring the
    HTTP flow's reactive refresh.
    """

    mode: str
    username: str
    secret: str
    provider: OAuth2TokenProvider | None = None


def _parse(schemes: Sequence[str], url: str) -> MailDSN:
    return parse_imap_dsn(url) if schemes is IMAP_SCHEMES else parse_smtp_dsn(url)


async def resolve_mail_auth(ctx: FetchContext, schemes: Sequence[str]) -> tuple[MailDSN, MailAuth]:
    """Resolve both the connection target and how to authenticate to it.

    When the context carries an OAuth2 ``auth_scheme`` (the same scheme the HTTP
    transport uses), authenticate with ``XOAUTH2`` using a token from the vault —
    the host / user / TLS mode come from the persisted (password-less) DSN.
    Otherwise fall back to password auth via :func:`resolve_dsn` (vault secret).

    Works for every context type (fetch / sense / write): all expose ``vault`` /
    ``auth_ref`` / ``base_url``, and ``auth_scheme`` defaults to ``None``.
    """
    scheme = getattr(ctx, "auth_scheme", None)
    if scheme is not None and getattr(scheme, "kind", None) == "oauth2":
        base = ctx.base_url or ""
        dsn = _parse(schemes, base) if is_dsn(base, schemes) else MailDSN("", 0, "", "", True, False)
        provider = OAuth2TokenProvider(ctx.vault, ctx.auth_ref, scheme)
        token = await provider.access_token()
        if token is None:
            token = await provider.refresh() or ""
        return dsn, MailAuth("xoauth2", dsn.username, token, provider)

    raw = await resolve_dsn(ctx, schemes)
    dsn = _parse(schemes, raw)
    return dsn, MailAuth("basic", dsn.username, dsn.password)


def xoauth2_string(username: str, access_token: str) -> str:
    """Build the SASL ``XOAUTH2`` initial-response string (pre-base64).

    Format per Google/Microsoft: ``user=<addr>^Aauth=Bearer <token>^A^A`` where
    ``^A`` is a 0x01 byte. The IMAP / SMTP libraries base64-encode it themselves.
    """
    return f"user={username}\x01auth=Bearer {access_token}\x01\x01"


async def resolve_mail_dsn(ctx: FetchContext, schemes: Sequence[str]) -> MailDSN:
    """Resolve just the connection string from the vault (password auth)."""
    raw = await resolve_dsn(ctx, schemes)
    return _parse(schemes, raw)
