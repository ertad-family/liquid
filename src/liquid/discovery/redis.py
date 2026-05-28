"""Redis discovery — group the keyspace into namespace endpoints.

Redis has no schema, so discovery samples keys and derives namespaces from the
prefix before the first ``:`` (the de-facto Redis convention, e.g. ``user:42``).
Each namespace becomes a read endpoint; keys without a ``:`` fall under a single
``/keys`` endpoint. The input is a ``redis://`` URL; any other URL returns
``None``.

Requires the ``redis`` extra (``pip install 'liquid-api[redis]'``); redis is
imported function-locally so the core stays dependency-free.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

from liquid.exceptions import DiscoveryError, Recovery
from liquid.models.schema import APISchema, AuthRequirement, Endpoint, EndpointKind
from liquid.transport._sql import is_dsn, redact_dsn
from liquid.transport.redis_driver import _REDIS_SCHEMES, _aclose

logger = logging.getLogger(__name__)

_SAMPLE = 1000


class RedisDiscovery:
    def __init__(self, *, sample: int = _SAMPLE) -> None:
        self.sample = sample

    async def discover(self, url: str) -> APISchema | None:
        if not is_dsn(url, _REDIS_SCHEMES):
            return None

        try:
            import redis.asyncio as redis_async
        except ImportError as e:
            raise DiscoveryError(
                "Redis discovery requires the 'redis' extra.",
                recovery=Recovery(hint="Install it: pip install 'liquid-api[redis]'", retry_safe=False),
            ) from e

        client = redis_async.from_url(url, decode_responses=True)
        try:
            keys = await _sample_keys(client, self.sample)
        except Exception as e:
            raise DiscoveryError(
                f"Could not scan Redis keyspace: {e}",
                recovery=Recovery(hint="Check the URL, credentials, and reachability.", retry_safe=True),
            ) from e
        finally:
            await _aclose(client)

        endpoints = _keys_to_endpoints(keys)
        if not endpoints:
            return None

        return APISchema(
            source_url=redact_dsn(url),
            service_name=_service_name(url),
            discovery_method="redis",
            endpoints=endpoints,
            auth=AuthRequirement(type="custom", tier="A"),
        )


async def _sample_keys(client: Any, sample: int) -> list[str]:
    keys: list[str] = []
    cursor = 0
    while True:
        cursor, batch = await client.scan(cursor=cursor, count=200)
        keys.extend(batch)
        if int(cursor) == 0 or len(keys) >= sample:
            break
    return keys


def _keys_to_endpoints(keys: list[str]) -> list[Endpoint]:
    counts: dict[str, int] = {}
    for key in keys:
        prefix = key.split(":", 1)[0] if ":" in key else ""
        counts[prefix] = counts.get(prefix, 0) + 1
    return [_namespace_endpoint(prefix, counts[prefix]) for prefix in sorted(counts)]


def _namespace_endpoint(prefix: str, count: int) -> Endpoint:
    path = f"/{prefix}" if prefix else "/keys"
    pattern = f"{prefix}:*" if prefix else "*"
    return Endpoint(
        path=path,
        method="GET",
        protocol="redis",
        kind=EndpointKind.READ,
        description=f"Redis keys matching '{pattern}' (~{count} sampled)",
        response_schema={
            "type": "object",
            "properties": {"key": {"type": "string"}, "type": {"type": "string"}, "value": {}},
        },
        transport_meta={"kind": "namespace", "prefix": prefix},
    )


def _service_name(url: str) -> str:
    host = urlsplit(url).hostname or "redis"
    return f"redis-{host}" if host != "redis" else "redis"
