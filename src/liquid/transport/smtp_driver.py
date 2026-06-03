"""SMTP transport driver — the efferent hand of the mail organ: compose and send.

Unlike the read/database drivers this one is write-only: ``write(op="insert")``
builds a MIME message from the ``values`` mapping (``to`` / ``subject`` / ``body``
/ ``cc`` / ``bcc`` / ``html`` / ``from`` / ``reply_to``) and hands it to the server.
``fetch`` is intentionally a 405 — reading is the IMAP driver's job.

Connection is an ``smtp://`` / ``smtps://`` DSN; the password is pulled from the
vault at call time and the persisted URL is credential-redacted. Built on the
stdlib :mod:`smtplib`, run inside :func:`asyncio.to_thread` so the blocking socket
work never stalls the event loop — no third-party dependency for password /
app-password auth.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any

from liquid.transport._mail import SMTP_SCHEMES, MailDSN, resolve_mail_dsn
from liquid.transport.base import DriverResponse, FetchContext, WriteContext

logger = logging.getLogger(__name__)


class SMTPDriver:
    scheme = "smtp"

    async def fetch(self, ctx: FetchContext) -> DriverResponse:
        # SMTP is send-only; reading a mailbox is the IMAP driver's job.
        return DriverResponse(status_code=405, error_body="SMTP is send-only; use the IMAP driver to read")

    async def write(self, ctx: WriteContext) -> DriverResponse:
        if ctx.op != "insert":
            return DriverResponse(status_code=400, error_body=f"unsupported op {ctx.op!r}; send is 'insert'")
        try:
            dsn = await resolve_mail_dsn(ctx, SMTP_SCHEMES)
        except Exception:
            return DriverResponse(status_code=503, error_body="no SMTP DSN")

        try:
            msg, recipients = build_message(ctx.values or {}, default_from=dsn.username)
        except ValueError as e:
            return DriverResponse(status_code=400, error_body=str(e))

        try:
            await asyncio.to_thread(_send_sync, dsn, msg, recipients)
        except Exception as e:
            return _map_smtp_error(e)

        return DriverResponse(
            status_code=200,
            records=[{"message_id": msg["Message-ID"], "accepted": len(recipients)}],
        )


def build_message(values: dict[str, Any], default_from: str) -> tuple[EmailMessage, list[str]]:
    """Build a MIME message and its full recipient list from ``values``.

    Raises :class:`ValueError` (mapped to a 400 by the caller) when required
    fields are missing — no sender, or no recipients.
    """
    sender = (values.get("from") or default_from or "").strip()
    if not sender:
        raise ValueError("no sender — set values.from or include a user in the SMTP DSN")

    to = _as_list(values.get("to"))
    cc = _as_list(values.get("cc"))
    bcc = _as_list(values.get("bcc"))
    recipients = to + cc + bcc
    if not recipients:
        raise ValueError("no recipients — set values.to (and optionally cc / bcc)")

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if values.get("reply_to"):
        msg["Reply-To"] = str(values["reply_to"])
    msg["Subject"] = str(values.get("subject", ""))
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()

    body = str(values.get("body", ""))
    html = values.get("html")
    msg.set_content(body)
    if html:
        msg.add_alternative(str(html), subtype="html")
    return msg, recipients


def _as_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list | tuple):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [part.strip() for part in str(raw).split(",") if part.strip()]


# --- blocking smtplib core (runs in a worker thread) ----------------------


def _send_sync(dsn: MailDSN, msg: EmailMessage, recipients: list[str]) -> None:
    server: smtplib.SMTP
    if dsn.use_ssl:
        server = smtplib.SMTP_SSL(dsn.host, dsn.port)
    else:
        server = smtplib.SMTP(dsn.host, dsn.port)
        if dsn.use_starttls:
            server.starttls()
    try:
        if dsn.username:
            server.login(dsn.username, dsn.password)
        server.send_message(msg, from_addr=msg["From"], to_addrs=recipients)
    finally:
        try:
            server.quit()
        except Exception:
            logger.debug("SMTP quit failed", exc_info=True)


def _map_smtp_error(e: Exception) -> DriverResponse:
    detail = str(e)[:500]
    if isinstance(e, smtplib.SMTPAuthenticationError):
        return DriverResponse(status_code=401, error_body=detail)
    if isinstance(e, smtplib.SMTPRecipientsRefused | smtplib.SMTPSenderRefused):
        return DriverResponse(status_code=400, error_body=detail)
    if isinstance(e, smtplib.SMTPException):
        return DriverResponse(status_code=502, error_body=detail)
    if isinstance(e, OSError | TimeoutError):
        return DriverResponse(status_code=503, error_body=detail)
    return DriverResponse(status_code=503, error_body=detail)
