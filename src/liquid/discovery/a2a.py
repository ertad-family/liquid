"""A2A (Agent-to-Agent) discovery via AgentCard.

Google's A2A protocol publishes an *AgentCard* — a JSON manifest at a well-known
URL (``/.well-known/agent.json`` historically, ``/.well-known/agent-card.json``
in the current spec) — describing the agent's name, URL endpoint, capabilities
and *skills*. Each skill is a callable unit (id, name, description, modes,
examples).

We turn each skill into an :class:`Endpoint` the :class:`~liquid.transport.a2a`
driver invokes via JSON-RPC. Same pluggable pattern as MCP/SOAP/gRPC: discovery
finds, driver runs, the rest of Liquid is unchanged.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from liquid.models.schema import (
    APISchema,
    AuthRequirement,
    Endpoint,
    EndpointKind,
    Parameter,
    ParameterLocation,
)

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)

# The current spec uses ``agent-card.json``; ``agent.json`` was the earlier name
# and is still served by older agents.
_AGENT_CARD_PATHS = ["/.well-known/agent-card.json", "/.well-known/agent.json"]


class A2ADiscovery:
    """Discovers A2A agents by fetching their AgentCard."""

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._external_client = http_client

    async def discover(self, url: str) -> APISchema | None:
        from liquid.discovery.utils import managed_http_client

        async with managed_http_client(self._external_client) as client:
            card = await self._fetch_card(client, url)
            if card is None:
                return None
            return self._parse(card, url)

    async def _fetch_card(self, client: httpx.AsyncClient, url: str) -> dict[str, Any] | None:
        # If the URL already points at the card, fetch it directly; otherwise try
        # the well-known mount points in order.
        base = url.rstrip("/")
        candidates: list[str] = []
        if any(base.endswith(p) for p in _AGENT_CARD_PATHS):
            candidates.append(base)
        candidates.extend(f"{base}{p}" for p in _AGENT_CARD_PATHS)

        for candidate in candidates:
            try:
                resp = await client.get(candidate, timeout=10.0, follow_redirects=True)
            except Exception:
                continue
            if not resp.is_success:
                continue
            try:
                data = resp.json()
            except ValueError:
                continue
            if _looks_like_agent_card(data):
                logger.info("A2A AgentCard discovered at %s", candidate)
                return data
        return None

    def _parse(self, card: dict[str, Any], source_url: str) -> APISchema:
        agent_url = card.get("url") or source_url.rstrip("/")
        skills = card.get("skills") or card.get("skillSet") or []

        endpoints: list[Endpoint] = []
        for skill in skills:
            if not isinstance(skill, dict):
                continue
            skill_id = skill.get("id") or skill.get("name", "")
            if not skill_id:
                continue
            description = skill.get("description") or skill.get("name") or ""
            # A2A skills don't have an input JSON Schema in the AgentCard — the
            # input is a free-form message. We expose a single ``message`` param
            # so the agent knows what to fill, and pass through anything else.
            params = [
                Parameter(
                    name="message",
                    location=ParameterLocation.BODY,
                    required=False,
                    description="Free-form message for the skill (any structured params are forwarded as-is).",
                )
            ]
            endpoints.append(
                Endpoint(
                    path=f"/a2a/skills/{skill_id}",
                    method="POST",
                    protocol="a2a",
                    description=str(description)[:500],
                    kind=EndpointKind.READ,
                    parameters=params,
                    transport_meta={
                        "agent_url": agent_url,
                        "skill_id": skill_id,
                        "skill_name": skill.get("name") or skill_id,
                    },
                )
            )

        return APISchema(
            source_url=source_url,
            service_name=card.get("name") or _infer_name(source_url),
            discovery_method="a2a",
            endpoints=endpoints,
            auth=AuthRequirement(type=_auth_type_from_card(card), tier="A"),
        )


def _looks_like_agent_card(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    # An AgentCard always names the agent and points at its endpoint URL; the
    # rest (skills, capabilities, defaultInputModes…) is optional/version-specific.
    return (
        "name" in data
        and ("url" in data or "endpointUrl" in data)
        and ("skills" in data or "capabilities" in data or "skillSet" in data)
    )


def _auth_type_from_card(card: dict[str, Any]) -> str:
    auth = card.get("authentication") or {}
    if isinstance(auth, dict):
        schemes = auth.get("schemes") or auth.get("type")
        if isinstance(schemes, list) and schemes:
            first = str(schemes[0]).lower()
            if "bearer" in first:
                return "bearer"
            if "apikey" in first or "api_key" in first:
                return "api_key"
            if "basic" in first:
                return "basic"
            if "oauth" in first:
                return "oauth2"
    return "bearer"


def _infer_name(url: str) -> str:
    from liquid.discovery.utils import infer_service_name

    return infer_service_name(url)
