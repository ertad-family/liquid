"""Redis transport driver — read a keyspace namespace as records.

Redis isn't tabular: keys are grouped by a ``prefix:`` namespace (discovery finds
them), and each key carries a typed value. The driver ``SCAN``s the namespace and
reads each key by type (GET / HGETALL / LRANGE / SMEMBERS / ZRANGE), yielding
``{key, type, value}`` records. Pagination is **native cursor-based**: the
fetch cursor *is* the Redis SCAN cursor, surfaced via ``next_cursor`` — unlike the
offset model of the SQL/Mongo drivers.

Connection is a ``redis://`` / ``rediss://`` URL (optionally with a ``/db``
number); the persisted URL is credential-redacted. Requires the ``redis`` extra
(``pip install 'liquid-api[redis]'``); redis is imported function-locally so the
core stays dependency-free.
"""

from __future__ import annotations

import logging
from typing import Any

from liquid.transport._sql import coerce_limit, coerce_value, resolve_dsn
from liquid.transport.base import DriverResponse, FetchContext

logger = logging.getLogger(__name__)

_REDIS_SCHEMES = ("redis://", "rediss://", "unix://")
_RESERVED = frozenset({"limit", "offset", "__cursor__", "match"})


class RedisDriver:
    scheme = "redis"

    async def fetch(self, ctx: FetchContext) -> DriverResponse:
        import redis.asyncio as redis_async

        meta = ctx.endpoint.transport_meta or {}
        params = ctx.params or {}
        try:
            url = await resolve_dsn(ctx, _REDIS_SCHEMES)
        except Exception:
            url = ctx.base_url or ""
        if not _is_redis_url(url):
            return DriverResponse(status_code=503, error_body="no Redis URL")

        count = coerce_limit(params.get("limit"))
        pattern = params.get("match") or _pattern(meta.get("prefix", ""))
        scan_cursor = _coerce_cursor(ctx.cursor)

        client = redis_async.from_url(url, decode_responses=True)
        try:
            new_cursor, keys = await client.scan(cursor=scan_cursor, match=pattern, count=count)
            records = [await _read_key(client, key) for key in keys]
        except Exception as e:
            return _map_redis_error(e)
        finally:
            await _aclose(client)

        # SCAN signals "done" by returning cursor 0.
        next_cursor = str(new_cursor) if int(new_cursor) != 0 else None
        return DriverResponse(status_code=200, records=records, next_cursor=next_cursor)


def _pattern(prefix: str) -> str:
    return f"{prefix}:*" if prefix else "*"


def _coerce_cursor(cursor: str | None) -> int:
    try:
        return max(0, int(cursor))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


async def _read_key(client: Any, key: str) -> dict[str, Any]:
    ktype = await client.type(key)
    value: Any
    if ktype == "string":
        value = await client.get(key)
    elif ktype == "hash":
        value = await client.hgetall(key)
    elif ktype == "list":
        value = await client.lrange(key, 0, -1)
    elif ktype == "set":
        value = sorted(await client.smembers(key))
    elif ktype == "zset":
        value = [{"member": m, "score": s} for m, s in await client.zrange(key, 0, -1, withscores=True)]
    else:
        value = f"<{ktype}>"  # stream / unsupported — don't pull heavy payloads
    return {"key": key, "type": ktype, "value": coerce_value(value)}


async def _aclose(client: Any) -> None:
    # redis-py >=5 prefers aclose(); fall back to close() on older versions.
    closer = getattr(client, "aclose", None) or getattr(client, "close", None)
    if closer is not None:
        await closer()


def _is_redis_url(url: Any) -> bool:
    return isinstance(url, str) and url.lower().startswith(_REDIS_SCHEMES)


def _map_redis_error(e: Exception) -> DriverResponse:
    from redis import exceptions as re

    detail = str(e)[:500]
    if isinstance(e, re.AuthenticationError):
        return DriverResponse(status_code=401, error_body=detail)
    if isinstance(e, (re.ConnectionError, re.TimeoutError)):
        return DriverResponse(status_code=503, error_body=detail)
    if isinstance(e, re.ResponseError):
        return DriverResponse(status_code=400, error_body=detail)
    return DriverResponse(status_code=503, error_body=detail)
