"""ADB driver — Android as senses & hands. Parsing is unit-tested; the real
subprocess path (fetch shell / write action / logcat sense) is exercised
end-to-end against a *fake* ``adb`` binary placed on PATH — deterministic, no
device or emulator needed."""

from __future__ import annotations

import os
import stat

import httpx
import pytest

from liquid.exceptions import VaultError
from liquid.models.schema import Endpoint
from liquid.sync.fetcher import Fetcher
from liquid.transport import get_driver, supports_sense, supports_write
from liquid.transport.adb_driver import _base, _parse_logcat, _serial
from liquid.transport.base import FetchContext, WriteContext


class FakeVault:
    async def get(self, key):
        raise VaultError(key)

    async def store(self, key, value): ...
    async def delete(self, key): ...


class _Ctx:
    def __init__(self, base_url):
        self.base_url = base_url


_EP = Endpoint(path="/logcat", protocol="adb", method="GET", transport_meta={})


# --- pure helpers ----------------------------------------------------------


def test_supports_sense_and_write():
    d = get_driver("adb")
    assert supports_sense(d) and supports_write(d)


def test_parse_logcat_threadtime():
    line = "06-01 12:00:00.123  1234  5678 I ActivityManager: Start proc"
    p = _parse_logcat(line)
    assert p["level"] == "I" and p["tag"] == "ActivityManager"
    assert p["message"] == "Start proc" and p["pid"] == 1234
    assert p["line"] == line


def test_parse_logcat_unstructured():
    assert _parse_logcat("--------- beginning of main") == {"line": "--------- beginning of main"}


def test_serial_network_and_device():
    assert _serial(_Ctx("adb://192.168.1.5:5555")) == "192.168.1.5:5555"
    assert _serial(_Ctx("adb://emulator-5554")) == "emulator-5554"
    assert _serial(_Ctx("adb://")) is None


def test_base_args():
    assert _base("emulator-5554") == ["adb", "-s", "emulator-5554"]
    assert _base(None) == ["adb"]


# --- fake-adb end-to-end ---------------------------------------------------

_FAKE_ADB = """#!/usr/bin/env python3
import sys, time
args = sys.argv[1:]
if args[:1] == ["-s"]:
    args = args[2:]
cmd = args[0] if args else ""
if cmd == "devices":
    print("List of devices attached")
    print("emulator-5554\\tdevice")
elif cmd == "connect":
    print("connected to " + (args[1] if len(args) > 1 else ""))
elif cmd == "shell":
    sub = " ".join(args[1:])
    if sub.startswith("getprop"):
        print("[ro.product.model]: [Pixel]")
    else:
        print("OK:" + sub)
elif cmd == "logcat":
    for i in range(3):
        print(f"06-01 12:00:0{i}.000  100  200 I FakeTag: line {i}")
        sys.stdout.flush()
        time.sleep(0.02)
"""


@pytest.fixture
def fake_adb(tmp_path, monkeypatch):
    script = tmp_path / "adb"
    script.write_text(_FAKE_ADB)
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    return script


async def test_fetch_runs_shell(fake_adb):
    drv = get_driver("adb")
    async with httpx.AsyncClient() as hc:
        ctx = FetchContext(
            endpoint=_EP,
            base_url="adb://emulator-5554",
            params={"command": "getprop"},
            headers={},
            cursor=None,
            selector=None,
            pagination=None,
            vault=FakeVault(),
            auth_ref="none",
            http_client=hc,
        )
        resp = await drv.fetch(ctx)
    assert resp.status_code == 200
    assert resp.records == [{"line": "[ro.product.model]: [Pixel]"}]


async def test_write_runs_action(fake_adb):
    drv = get_driver("adb")
    ctx = WriteContext(
        endpoint=_EP,
        base_url="adb://emulator-5554",
        op="insert",
        values={"command": "input tap 100 200"},
        where={},
        vault=FakeVault(),
        auth_ref="none",
    )
    resp = await drv.write(ctx)
    assert resp.status_code == 200
    assert resp.records[0]["output"] == "OK:input tap 100 200"


async def test_write_requires_command(fake_adb):
    drv = get_driver("adb")
    ctx = WriteContext(
        endpoint=_EP,
        base_url="adb://emulator-5554",
        op="insert",
        values={},
        where={},
        vault=FakeVault(),
        auth_ref="none",
    )
    assert (await drv.write(ctx)).status_code == 400


async def test_sense_streams_logcat(fake_adb):
    async with httpx.AsyncClient() as hc:
        f = Fetcher(http_client=hc, vault=FakeVault())
        stream = await f.sense(_EP, "adb://emulator-5554", "none", max_events=3, max_seconds=8)
        seen = [e async for e in stream]
    assert len(seen) == 3
    assert [e.payload["message"] for e in seen] == ["line 0", "line 1", "line 2"]
    assert all(e.modality == "message" and e.payload["tag"] == "FakeTag" for e in seen)
    assert all(e.source == "emulator-5554" for e in seen)
