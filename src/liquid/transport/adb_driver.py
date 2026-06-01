"""ADB transport driver — perceive and act on an Android device.

The Android Debug Bridge reaches phones, tablets, Android TV boxes, kiosks and
embedded HMIs. It maps cleanly onto Liquid's shape:

* ``sense`` — stream ``adb logcat`` and yield each log line as a
  ``modality="message"`` :class:`SenseEvent` (parsed into ``level`` / ``tag`` /
  ``message`` when it's the threadtime format). A live event stream — true push
  from the device, the afferent organ pointed at Android.
* ``fetch`` — run a shell command (``getprop``, ``dumpsys battery``, …) and
  return its output lines as records.
* ``write`` — run a shell *action* (``input tap 100 200``, ``am start -n …``,
  ``input text …``) — the hands.

Connection is an ``adb://[serial]`` URL where ``serial`` is a device id
(``emulator-5554``) or a network ``host:port`` (``adb://192.168.1.5:5555`` —
connected automatically); ``adb://`` targets the single attached device. This
shells out to the system ``adb`` binary (no Python dependency — like the SQL
Server driver relies on a system ODBC driver); ``adb`` must be on ``PATH``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import shlex
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from liquid.transport.base import DriverResponse, FetchContext, SenseContext, SenseEvent, WriteContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

# logcat -v threadtime: "MM-DD HH:MM:SS.mmm  PID  TID L Tag: message"
_LOGCAT_RE = re.compile(
    r"^(?P<ts>\d\d-\d\d \d\d:\d\d:\d\d\.\d+)\s+(?P<pid>\d+)\s+(?P<tid>\d+)\s+"
    r"(?P<level>[VDIWEFS])\s+(?P<tag>.*?)\s*:\s?(?P<message>.*)$"
)
_DEFAULT_SHELL = "getprop"


class ADBDriver:
    scheme = "adb"

    async def fetch(self, ctx: FetchContext) -> DriverResponse:
        serial = _serial(ctx)
        meta = ctx.endpoint.transport_meta or {}
        command = (ctx.params or {}).get("command") or meta.get("command") or _DEFAULT_SHELL
        try:
            await _ensure_connected(serial)
            rc, out = await _run([*_base(serial), "shell", *shlex.split(command)], timeout=20.0)
        except FileNotFoundError:
            return DriverResponse(status_code=501, error_body="adb not found on PATH (install Android platform-tools)")
        except Exception as e:
            return DriverResponse(status_code=503, error_body=f"adb error: {e}"[:500])
        if rc != 0:
            return DriverResponse(status_code=502, error_body=out[:500] or f"adb shell exited {rc}")
        records = [{"line": line} for line in out.splitlines() if line.strip()]
        return DriverResponse(status_code=200, records=records)

    async def write(self, ctx: WriteContext) -> DriverResponse:
        serial = _serial(ctx)
        values = ctx.values or {}
        if ctx.op not in ("insert", "update"):
            return DriverResponse(
                status_code=400, error_body=f"unsupported op {ctx.op!r} (ADB writes run a shell action)"
            )
        command = values.get("command")
        if not command:
            return DriverResponse(status_code=400, error_body="values.command is required (e.g. 'input tap 100 200')")
        try:
            await _ensure_connected(serial)
            rc, out = await _run([*_base(serial), "shell", *shlex.split(command)], timeout=20.0)
        except FileNotFoundError:
            return DriverResponse(status_code=501, error_body="adb not found on PATH")
        except Exception as e:
            return DriverResponse(status_code=503, error_body=f"adb error: {e}"[:500])
        if rc != 0:
            return DriverResponse(status_code=502, error_body=out[:500] or f"adb shell exited {rc}")
        return DriverResponse(status_code=200, records=[{"command": command, "output": out.strip()}])

    async def sense(self, ctx: SenseContext) -> AsyncIterator[SenseEvent]:
        """Perceive the device's log stream via ``adb logcat`` — live push.

        Streams ``logcat -v threadtime`` (optionally filtered by
        ``params['filter']`` / ``transport_meta['filter']``, e.g.
        ``"ActivityManager:I *:S"``) and yields each line as a
        ``modality="message"`` event parsed into ``level`` / ``tag`` / ``message``
        (raw ``line`` always present). Bounded by ``max_events`` / ``max_seconds``;
        the logcat process is terminated when the stream ends.
        """
        serial = _serial(ctx)
        meta = ctx.endpoint.transport_meta or {}
        filt = (ctx.params or {}).get("filter") or meta.get("filter")
        args = [*_base(serial), "logcat", "-v", "threadtime"]
        if filt:
            args += shlex.split(filt)

        emitted = 0
        loop = asyncio.get_running_loop()
        deadline = (loop.time() + ctx.max_seconds) if ctx.max_seconds is not None else None
        proc = None
        try:
            await _ensure_connected(serial)
            proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
            )
            while True:
                timeout = None
                if deadline is not None:
                    timeout = max(0.0, deadline - loop.time())
                    if timeout == 0.0:
                        return
                try:
                    raw = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
                except TimeoutError:
                    return
                if not raw:  # EOF (device gone / logcat exited)
                    return
                line = raw.decode("utf-8", errors="replace").rstrip("\n")
                if not line.strip():
                    continue
                yield SenseEvent(source=serial or "adb", modality="message", payload=_parse_logcat(line))
                emitted += 1
                if ctx.max_events is not None and emitted >= ctx.max_events:
                    return
        except FileNotFoundError:
            logger.debug("adb not found on PATH")
            return
        except Exception:
            logger.debug("ADB sense stream ended on error", exc_info=True)
            return
        finally:
            if proc is not None and proc.returncode is None:
                with contextlib.suppress(Exception):
                    proc.terminate()


def _serial(ctx: Any) -> str | None:
    url = getattr(ctx, "base_url", "") or ""
    if not (isinstance(url, str) and url.lower().startswith("adb://")):
        return None
    # Use the raw netloc (case-preserving — device serials can be mixed-case —
    # and it already carries any host:port for network devices).
    serial = urlsplit(url).netloc
    return serial or None


def _base(serial: str | None) -> list[str]:
    return ["adb", "-s", serial] if serial else ["adb"]


async def _ensure_connected(serial: str | None) -> None:
    """Network devices (host:port serials) need an explicit `adb connect` first."""
    if serial and ":" in serial:
        with contextlib.suppress(Exception):
            await _run(["adb", "connect", serial], timeout=15.0)


async def _run(args: list[str], timeout: float) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        with contextlib.suppress(Exception):
            proc.kill()
        raise
    return proc.returncode or 0, out.decode("utf-8", errors="replace")


def _parse_logcat(line: str) -> dict[str, Any]:
    m = _LOGCAT_RE.match(line)
    if not m:
        return {"line": line}
    return {
        "line": line,
        "timestamp": m.group("ts"),
        "pid": int(m.group("pid")),
        "level": m.group("level"),
        "tag": m.group("tag").strip(),
        "message": m.group("message"),
    }
