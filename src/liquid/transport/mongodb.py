"""MongoDB transport driver — read documents from a discovered collection.

A document store isn't SQL: a collection is the unit (like a table), but there's
no fixed schema. The driver runs ``find(filter).skip().limit()`` — equality
filters on fields (scalar values only, so no operator-object injection), offset
pagination (cursor = next offset). Documents are returned as JSON-friendly dicts
(ObjectId → str, dates → ISO).

Connection is a ``mongodb://`` / ``mongodb+srv://`` URI carrying the database; the
persisted URI is credential-redacted and the password resolved from the vault at
fetch. Requires the ``mongodb`` extra (``pip install 'liquid-api[mongodb]'``);
pymongo is imported function-locally so the core stays dependency-free.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

from liquid.transport._sql import DSNError, coerce_limit, coerce_offset, coerce_value, resolve_dsn
from liquid.transport.base import DriverResponse, FetchContext

logger = logging.getLogger(__name__)

_MONGO_SCHEMES = ("mongodb://", "mongodb+srv://")
_RESERVED = frozenset({"limit", "offset", "__cursor__"})


class MongoDBDriver:
    scheme = "mongodb"

    async def fetch(self, ctx: FetchContext) -> DriverResponse:
        from pymongo import AsyncMongoClient

        meta = ctx.endpoint.transport_meta or {}
        collection = meta.get("collection")
        if not collection:
            return DriverResponse(status_code=400, error_body="no collection in endpoint metadata")

        try:
            uri = await resolve_dsn(ctx, _MONGO_SCHEMES)
        except DSNError as e:
            return DriverResponse(status_code=401, error_body=str(e)[:500])
        database = meta.get("database") or _database_from_uri(uri)
        if not database:
            return DriverResponse(status_code=400, error_body="MongoDB URI must include a database")

        params = ctx.params or {}
        limit = coerce_limit(params.get("limit"))
        offset = coerce_offset(ctx.cursor, params.get("offset"))
        query = _build_filter(params)

        client: Any = AsyncMongoClient(uri)
        try:
            cursor = client[database][collection].find(query).skip(offset).limit(limit)
            docs = [doc async for doc in cursor]
        except Exception as e:
            return _map_mongo_error(e)
        finally:
            await client.close()

        records = [_coerce_doc(d) for d in docs]
        next_cursor = str(offset + limit) if len(docs) >= limit else None
        return DriverResponse(status_code=200, records=records, next_cursor=next_cursor)


def _build_filter(params: dict[str, Any]) -> dict[str, Any]:
    """Equality filter from scalar params. Dict values are skipped so a caller
    can't smuggle in query operators (``{"$where": …}``)."""
    out: dict[str, Any] = {}
    for key, value in params.items():
        if key in _RESERVED or isinstance(value, dict):
            continue
        out[key] = value
    return out


def _database_from_uri(uri: str) -> str | None:
    return (urlsplit(uri).path or "").lstrip("/").split("/", 1)[0] or None


def _coerce_doc(doc: dict[str, Any]) -> dict[str, Any]:
    return {k: _coerce_mongo(v) for k, v in doc.items()}


def _coerce_mongo(v: Any) -> Any:
    # ObjectId (and other bson scalar types) stringify cleanly; recurse containers.
    if type(v).__name__ == "ObjectId":
        return str(v)
    if isinstance(v, dict):
        return {k: _coerce_mongo(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_coerce_mongo(x) for x in v]
    return coerce_value(v)


def _map_mongo_error(e: Exception) -> DriverResponse:
    from pymongo import errors as me

    detail = str(e)[:500]
    if isinstance(e, me.OperationFailure):
        code = getattr(e, "code", None)
        if code == 18:  # AuthenticationFailed
            return DriverResponse(status_code=401, error_body=detail)
        if code in (13, 8000):  # Unauthorized / Atlas auth
            return DriverResponse(status_code=403, error_body=detail)
        return DriverResponse(status_code=400, error_body=detail)
    if isinstance(e, (me.ServerSelectionTimeoutError, me.ConnectionFailure)):
        return DriverResponse(status_code=503, error_body=detail)
    if isinstance(e, me.PyMongoError):
        return DriverResponse(status_code=400, error_body=detail)
    return DriverResponse(status_code=503, error_body=detail)
