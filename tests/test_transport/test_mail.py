"""Mail drivers: DSN parsing, message normalization, MIME build, error mapping (pure)."""

from __future__ import annotations

import imaplib
import smtplib
from email.message import EmailMessage

import pytest

from liquid.discovery.email import _parse_mailbox_line
from liquid.transport._mail import parse_imap_dsn, parse_smtp_dsn
from liquid.transport.imap_driver import (
    _coerce_uid_cursor,
    _extract_body,
    _map_imap_error,
    _parse_fetch_item,
    message_to_record,
)
from liquid.transport.smtp_driver import _map_smtp_error, build_message

# --- DSN parsing ----------------------------------------------------------


def test_parse_imap_dsn_defaults_to_ssl():
    d = parse_imap_dsn("imap://alice%40ex.com:app%20pass@imap.ex.com/INBOX")
    assert (d.host, d.port, d.username, d.password) == ("imap.ex.com", 993, "alice@ex.com", "app pass")
    assert d.use_ssl and not d.use_starttls
    assert d.mailbox == "INBOX"


def test_parse_imap_dsn_starttls_on_143():
    d = parse_imap_dsn("imap://u:p@host:143")
    assert d.port == 143 and not d.use_ssl and d.use_starttls
    assert d.mailbox == "INBOX"  # no path -> default folder


def test_parse_imap_dsn_imaps_scheme():
    assert parse_imap_dsn("imaps://u:p@host:143").use_ssl is True


def test_parse_smtp_dsn_submission_port_starttls():
    s = parse_smtp_dsn("smtp://bob:pw@smtp.ex.com")
    assert s.port == 587 and not s.use_ssl and s.use_starttls


def test_parse_smtp_dsn_implicit_tls():
    assert parse_smtp_dsn("smtp://u:p@h:465").use_ssl is True
    assert parse_smtp_dsn("smtps://u:p@h:587").use_ssl is True


# --- IMAP message normalization ------------------------------------------


def test_coerce_uid_cursor():
    assert _coerce_uid_cursor(None) == 0
    assert _coerce_uid_cursor("42") == 42
    assert _coerce_uid_cursor("garbage") == 0
    assert _coerce_uid_cursor("-5") == 0


def _sample_message() -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = "Alice <alice@ex.com>"
    msg["To"] = "bob@ex.com"
    msg["Subject"] = "Hello"
    msg["Message-ID"] = "<abc@ex.com>"
    msg["Date"] = "Tue, 03 Jun 2026 10:00:00 +0000"
    msg.set_content("plain body here")
    return msg


def test_message_to_record():
    rec = message_to_record(7, ["\\Seen"], _sample_message())
    assert rec["uid"] == "7"
    assert rec["from"] == "Alice <alice@ex.com>"
    assert rec["to"] == "bob@ex.com"
    assert rec["subject"] == "Hello"
    assert rec["message_id"] == "<abc@ex.com>"
    assert rec["flags"] == ["\\Seen"]
    assert "plain body here" in rec["body"]


def test_extract_body_prefers_plain():
    msg = EmailMessage()
    msg["Subject"] = "x"
    msg.set_content("the plain part")
    msg.add_alternative("<p>the html part</p>", subtype="html")
    assert "the plain part" in _extract_body(msg)


def test_parse_fetch_item_roundtrip():
    raw = _sample_message().as_bytes()
    msgdata = [(b"7 (FLAGS (\\Seen) BODY[] {%d}" % len(raw), raw), b")"]
    rec = _parse_fetch_item(7, msgdata)
    assert rec is not None
    assert rec["uid"] == "7" and rec["subject"] == "Hello"
    assert "\\Seen" in rec["flags"]


def test_parse_fetch_item_no_payload_returns_none():
    assert _parse_fetch_item(1, [b"1 (FLAGS (\\Seen))"]) is None


# --- mailbox LIST parsing -------------------------------------------------


def test_parse_mailbox_line():
    assert _parse_mailbox_line(b'(\\HasNoChildren) "/" "INBOX"') == "INBOX"
    assert _parse_mailbox_line(b'(\\HasChildren) "." "[Gmail]/Sent Mail"') == "[Gmail]/Sent Mail"
    assert _parse_mailbox_line(b"") is None
    assert _parse_mailbox_line(None) is None


# --- SMTP MIME build ------------------------------------------------------


def test_build_message_recipients_and_headers():
    msg, rcpts = build_message(
        {"to": "a@x.com, b@y.com", "cc": "c@z.com", "bcc": ["d@w.com"], "subject": "hi", "body": "yo"},
        default_from="me@x.com",
    )
    assert msg["From"] == "me@x.com"
    assert msg["To"] == "a@x.com, b@y.com"
    assert msg["Cc"] == "c@z.com"
    assert msg["Subject"] == "hi"
    assert msg["Message-ID"]
    assert rcpts == ["a@x.com", "b@y.com", "c@z.com", "d@w.com"]


def test_build_message_html_alternative():
    msg, _ = build_message({"to": "a@x.com", "body": "txt", "html": "<b>hi</b>"}, default_from="me@x.com")
    assert msg.is_multipart()


def test_build_message_requires_sender():
    with pytest.raises(ValueError, match="sender"):
        build_message({"to": "a@x.com"}, default_from="")


def test_build_message_requires_recipients():
    with pytest.raises(ValueError, match="recipient"):
        build_message({"subject": "x"}, default_from="me@x.com")


# --- error mapping --------------------------------------------------------


def test_map_imap_error():
    assert _map_imap_error(imaplib.IMAP4.error("AUTHENTICATIONFAILED")).status_code == 401
    assert _map_imap_error(imaplib.IMAP4.error("BAD command")).status_code == 400
    assert _map_imap_error(ConnectionError("refused")).status_code == 503


def test_map_smtp_error():
    assert _map_smtp_error(smtplib.SMTPAuthenticationError(535, b"bad")).status_code == 401
    assert _map_smtp_error(smtplib.SMTPRecipientsRefused({})).status_code == 400
    assert _map_smtp_error(smtplib.SMTPException("boom")).status_code == 502
    assert _map_smtp_error(OSError("down")).status_code == 503


# --- driver-level behaviour -----------------------------------------------


@pytest.mark.asyncio
async def test_smtp_fetch_is_405():
    from liquid.transport.smtp_driver import SMTPDriver

    resp = await SMTPDriver().fetch(None)  # type: ignore[arg-type]
    assert resp.status_code == 405
