from __future__ import annotations

import logging

from liquid.models.schema import APISchema  # noqa: TC001

logger = logging.getLogger(__name__)


class MCPDiscovery:
    """Discovers APIs by connecting to an MCP server.

    MCP (Model Context Protocol) servers publish tools and resources
    with structured types and descriptions. This is the cheapest
    and most reliable discovery method.

    Note: Full implementation depends on MCP SDK availability.
    Currently a stub that returns None.
    """

    async def discover(self, url: str) -> APISchema | None:
        logger.debug("MCPDiscovery is not yet implemented, skipping for %s", url)
        return None
