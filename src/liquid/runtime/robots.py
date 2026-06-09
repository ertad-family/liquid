"""robots.txt gate for the HTML-scrape driver — honest crawling by default.

Liquid's scrape driver does recurring, unattended traffic (sync + sense), which
is crawler behaviour, so it honours the Robots Exclusion Protocol by default. The
policy is **letter + honest identification**: we declare a real ``LiquidBot``
user-agent (never a spoofed browser) and obey the directives written for our
token. AI-specific bans (``GPTBot``, ``ClaudeBot``, …) target other tokens and so
don't apply to us by the letter; a deployment that wants to honour their *spirit*
can run a stricter policy on top.

Fetching is done through the caller's shared (SSRF-guarded) httpx client; parsing
and matching use the stdlib :class:`~urllib.robotparser.RobotFileParser`. A
missing/4xx robots.txt means "no restriction" (the RFC 9309 convention); a fetch
error is treated leniently as allowed so a transient blip doesn't silently halt a
legitimate sync. robots.txt itself is always fetchable.

Respect is on by default and overridable — ``LIQUID_RESPECT_ROBOTS=false`` env or
``transport_meta["respect_robots"]=false`` per adapter — for the legitimate cases
(you own the site, a data contract, entitled open-data).
"""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)

_TTL = 3600.0  # re-fetch a host's robots.txt at most hourly


def default_user_agent() -> str:
    """An honest, identifiable bot UA — the antithesis of a spoofed browser."""
    try:
        from importlib.metadata import version

        v = version("liquid-api")
    except Exception:
        v = "0"
    return f"LiquidBot/{v} (+https://github.com/ertad-family/liquid)"


def respect_robots(meta: dict | None) -> bool:
    """Resolve the effective policy: per-adapter override wins over the env default.

    Default is ON (absence of the env var = respect). The escape hatch exists for
    sites you own or are entitled to scrape.
    """
    override = (meta or {}).get("respect_robots")
    if override is not None:
        return bool(override)
    return os.environ.get("LIQUID_RESPECT_ROBOTS", "true").strip().lower() not in ("0", "false", "no", "off")


class RobotsGate:
    """Per-origin robots.txt cache + checker for our declared user-agent."""

    def __init__(self, user_agent: str | None = None, *, ttl: float = _TTL) -> None:
        self.user_agent = user_agent or default_user_agent()
        # Match against the bare token (what sites write on their User-agent
        # lines), e.g. "LiquidBot" from "LiquidBot/0.1 (+url)".
        self.token = self.user_agent.split("/", 1)[0].split()[0]
        self._ttl = ttl
        self._cache: dict[str, tuple[RobotFileParser | None, float]] = {}

    async def _parser(self, client: httpx.AsyncClient, url: str) -> RobotFileParser | None:
        split = urlsplit(url)
        origin = f"{split.scheme}://{split.netloc}"
        now = time.monotonic()
        hit = self._cache.get(origin)
        if hit is not None and hit[1] > now:
            return hit[0]

        parser: RobotFileParser | None
        try:
            resp = await client.get(
                f"{origin}/robots.txt",
                headers={"User-Agent": self.user_agent},
                follow_redirects=True,
            )
            if resp.status_code >= 400:
                parser = None  # no usable robots.txt → unrestricted (RFC 9309)
            else:
                parser = RobotFileParser()
                parser.parse(resp.text.splitlines())
        except Exception:
            # Lenient: a transient fetch failure shouldn't halt a legitimate sync.
            logger.debug("robots.txt fetch failed for %s — treating as allowed", origin, exc_info=True)
            parser = None

        self._cache[origin] = (parser, now + self._ttl)
        return parser

    async def allowed(self, client: httpx.AsyncClient, url: str) -> bool:
        parser = await self._parser(client, url)
        if parser is None:
            return True
        try:
            return parser.can_fetch(self.token, url)
        except Exception:
            return True

    async def crawl_delay(self, client: httpx.AsyncClient, url: str) -> float | None:
        """The Crawl-delay for our token, if the host sets one — a politeness floor."""
        parser = await self._parser(client, url)
        if parser is None:
            return None
        try:
            delay = parser.crawl_delay(self.token)
        except Exception:
            return None
        return float(delay) if delay is not None else None
