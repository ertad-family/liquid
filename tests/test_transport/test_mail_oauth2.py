"""Mail OAuth2 / XOAUTH2: token provider, auth resolution, refresh-retry (Phase 2)."""

from __future__ import annotations

import imaplib
import smtplib
import types
import unittest.mock
from email.message import EmailMessage

import httpx
import pytest

from liquid.auth.oauth2 import OAuth2TokenProvider
from liquid.auth.schemes import OAuth2Auth
from liquid.exceptions import VaultError
from liquid.transport import imap_driver, smtp_driver
from liquid.transport._mail import (
    IMAP_SCHEMES,
    MailAuth,
    MailDSN,
    resolve_mail_auth,
    xoauth2_string,
)


class FakeVault:
    def __init__(self, data: dict[str, str] | None = None) -> None:
        self.data = dict(data or {})

    async def store(self, key: str, value: str) -> None:
        self.data[key] = value

    async def get(self, key: str) -> str:
        if key not in self.data:
            raise VaultError(f"missing: {key}")
        return self.data[key]

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)


def _ctx(*, vault: FakeVault, auth_ref: str = "k", base_url: str = "", auth_scheme=None):
    return types.SimpleNamespace(vault=vault, auth_ref=auth_ref, base_url=base_url, auth_scheme=auth_scheme)


# --- XOAUTH2 string -------------------------------------------------------


def test_xoauth2_string_format():
    assert xoauth2_string("u@x.com", "TOK") == "user=u@x.com\x01auth=Bearer TOK\x01\x01"


# --- auth resolution ------------------------------------------------------


async def test_resolve_mail_auth_basic():
    vault = FakeVault({"k": "imap://u:p@imap.ex.com/INBOX"})
    dsn, auth = await resolve_mail_auth(_ctx(vault=vault), IMAP_SCHEMES)
    assert auth.mode == "basic"
    assert (auth.username, auth.secret) == ("u", "p")
    assert auth.provider is None
    assert dsn.host == "imap.ex.com"


async def test_resolve_mail_auth_xoauth2():
    vault = FakeVault({"k/access_token": "AT"})
    ctx = _ctx(vault=vault, base_url="imap://me@imap.gmail.com", auth_scheme=OAuth2Auth())
    dsn, auth = await resolve_mail_auth(ctx, IMAP_SCHEMES)
    assert auth.mode == "xoauth2"
    assert auth.username == "me"
    assert auth.secret == "AT"
    assert auth.provider is not None
    assert dsn.host == "imap.gmail.com"


# --- token provider -------------------------------------------------------


async def test_provider_access_token_missing_is_none():
    prov = OAuth2TokenProvider(FakeVault(), "k", OAuth2Auth())
    assert await prov.access_token() is None


async def test_provider_refresh_roundtrip():
    vault = FakeVault({"k/refresh_token": "rtok", "k/client_id": "cid", "k/client_secret": "csec"})
    cfg = OAuth2Auth(token_url="https://auth.example/token", scope="mail")
    prov = OAuth2TokenProvider(vault, "k", cfg)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "new", "refresh_token": "rtok2"})

    original = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(handler))
        return original(*args, **kwargs)

    with unittest.mock.patch("httpx.AsyncClient", patched):
        access = await prov.refresh()

    assert access == "new"
    assert vault.data["k/access_token"] == "new"
    assert vault.data["k/refresh_token"] == "rtok2"


async def test_provider_refresh_without_token_url_is_none():
    assert await OAuth2TokenProvider(FakeVault(), "k", OAuth2Auth()).refresh() is None


# --- IMAP refresh-retry ---------------------------------------------------


async def test_imap_fetch_with_refresh_retries(monkeypatch):
    calls = {"n": 0}

    def fake_search(dsn, auth, mailbox, since, limit):
        calls["n"] += 1
        if calls["n"] == 1:
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        return ([{"uid": "5"}], 5)

    monkeypatch.setattr(imap_driver, "_search_fetch_sync", fake_search)

    async def fake_refresh():
        return "newtok"

    auth = MailAuth("xoauth2", "me", "oldtok", types.SimpleNamespace(refresh=fake_refresh))
    dsn = MailDSN("h", 993, "me", "", True, False)
    records, last = await imap_driver._fetch_with_refresh(dsn, auth, "INBOX", 0, 50)

    assert calls["n"] == 2
    assert auth.secret == "newtok"
    assert records == [{"uid": "5"}] and last == 5


async def test_imap_fetch_no_provider_reraises(monkeypatch):
    def fake_search(dsn, auth, mailbox, since, limit):
        raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")

    monkeypatch.setattr(imap_driver, "_search_fetch_sync", fake_search)
    auth = MailAuth("basic", "u", "p", None)
    dsn = MailDSN("h", 993, "u", "p", True, False)
    with pytest.raises(imaplib.IMAP4.error):
        await imap_driver._fetch_with_refresh(dsn, auth, "INBOX", 0, 50)


# --- SMTP refresh-retry ---------------------------------------------------


async def test_smtp_send_with_refresh_retries(monkeypatch):
    calls = {"n": 0}

    def fake_send(dsn, auth, msg, recipients):
        calls["n"] += 1
        if calls["n"] == 1:
            raise smtplib.SMTPAuthenticationError(535, b"bad")
        return None

    monkeypatch.setattr(smtp_driver, "_send_sync", fake_send)

    async def fake_refresh():
        return "newtok"

    auth = MailAuth("xoauth2", "me", "old", types.SimpleNamespace(refresh=fake_refresh))
    dsn = MailDSN("h", 587, "me", "", False, True)
    msg = EmailMessage()
    msg["From"] = "me@x.com"
    await smtp_driver._send_with_refresh(dsn, auth, msg, ["a@x.com"])

    assert calls["n"] == 2
    assert auth.secret == "newtok"
