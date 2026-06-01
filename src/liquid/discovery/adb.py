"""ADB (Android) discovery.

Triggers on ``adb://``. Confirms a device is attached (``adb connect`` first for a
network ``host:port`` serial, then ``adb devices``) and returns a single
``protocol="adb"`` endpoint the driver can read (``shell``), act on (``shell``
actions) and perceive (``logcat``). There's no richer introspection — Android
exposes its surface through shell/logcat, not a manifest.

Shells out to the system ``adb`` binary; if it isn't on ``PATH`` (or no device is
attached) discovery returns ``None``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from urllib.parse import urlsplit

from liquid.models.schema import APISchema, AuthRequirement, Endpoint, EndpointKind

logger = logging.getLogger(__name__)


class ADBDiscovery:
    """Confirms an Android device is reachable over ADB and exposes a logcat/shell endpoint."""

    async def discover(self, url: str) -> APISchema | None:
        if not url.startswith("adb://"):
            return None
        serial = urlsplit(url).netloc or None
        try:
            if serial and ":" in serial:
                with contextlib.suppress(Exception):
                    await _run(["adb", "connect", serial])
            _rc, out = await _run(["adb", "devices"])
        except FileNotFoundError:
            logger.warning("adb URL given but the 'adb' binary is not on PATH (install Android platform-tools)")
            return None
        except Exception as e:
            logger.info("adb devices failed for %s: %s", url, e)
            return None

        devices = _parse_devices(out)
        if not devices:
            logger.info("no ADB devices attached for %s", url)
            return None
        if serial and serial not in devices:
            logger.info("ADB device %s not attached (have: %s)", serial, devices)
            return None

        endpoint = Endpoint(
            path="/logcat",
            method="GET",
            protocol="adb",
            kind=EndpointKind.READ,
            description="Android device — logcat stream (sense) + shell (fetch/act)",
            response_schema={
                "type": "object",
                "properties": {"line": {"type": "string"}, "tag": {"type": "string"}, "message": {"type": "string"}},
            },
            transport_meta={},
        )
        return APISchema(
            source_url=url,
            service_name=serial or (devices[0] if devices else "android"),
            discovery_method="adb",
            endpoints=[endpoint],
            auth=AuthRequirement(type="custom", tier="A"),
        )


async def _run(args: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
    return proc.returncode or 0, out.decode("utf-8", errors="replace")


def _parse_devices(out: str) -> list[str]:
    """Parse `adb devices` output → serials in the `device` state (ready)."""
    serials = []
    for line in out.splitlines()[1:]:  # skip "List of devices attached"
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials
