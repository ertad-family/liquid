"""MongoDB discovery — turn each collection into a read endpoint.

There's no fixed schema, so fields are inferred by sampling a few documents per
collection (union of top-level keys). The input is a ``mongodb://`` URI carrying
the database; any other URL returns ``None``. The persisted ``source_url`` is
credential-redacted.

Requires the ``mongodb`` extra (``pip install 'liquid-api[mongodb]'``); pymongo is
imported function-locally so the core stays dependency-free.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

from liquid.exceptions import DiscoveryError, Recovery
from liquid.models.schema import APISchema, AuthRequirement, Endpoint, EndpointKind
from liquid.transport._sql import is_dsn, redact_dsn

logger = logging.getLogger(__name__)

_MONGO_SCHEMES = ("mongodb://", "mongodb+srv://")
_SAMPLE = 25
_SYSTEM_PREFIX = "system."


class MongoDBDiscovery:
    def __init__(self, *, sample: int = _SAMPLE) -> None:
        self.sample = sample

    async def discover(self, url: str) -> APISchema | None:
        if not is_dsn(url, _MONGO_SCHEMES):
            return None

        try:
            from pymongo import AsyncMongoClient
        except ImportError as e:
            raise DiscoveryError(
                "MongoDB discovery requires the 'mongodb' extra.",
                recovery=Recovery(hint="Install it: pip install 'liquid-api[mongodb]'", retry_safe=False),
            ) from e

        database = (urlsplit(url).path or "").lstrip("/").split("/", 1)[0]
        if not database:
            raise DiscoveryError(
                "MongoDB URI must include a database (mongodb://host/dbname).",
                recovery=Recovery(hint="Append /<database> to the URI.", retry_safe=False),
            )

        client: Any = AsyncMongoClient(url)
        try:
            db = client[database]
            names = [n for n in await db.list_collection_names() if not n.startswith(_SYSTEM_PREFIX)]
            collections: list[tuple[str, list[str]]] = []
            for name in sorted(names):
                fields = await _sample_fields(db[name], self.sample)
                collections.append((name, fields))
        except Exception as e:
            raise DiscoveryError(
                f"Could not introspect MongoDB: {e}",
                recovery=Recovery(hint="Check the URI, credentials, and reachability.", retry_safe=True),
            ) from e
        finally:
            await client.close()

        endpoints = [_collection_endpoint(database, name, fields) for name, fields in collections]
        if not endpoints:
            return None

        return APISchema(
            source_url=redact_dsn(url),
            service_name=database,
            discovery_method="mongodb",
            endpoints=endpoints,
            auth=AuthRequirement(type="basic", tier="B"),
        )


async def _sample_fields(collection: Any, sample: int) -> list[str]:
    """Union of top-level keys across a few sampled documents."""
    seen: dict[str, None] = {}
    async for doc in collection.find().limit(sample):
        for key in doc:
            seen.setdefault(key, None)
    return list(seen)


def _collection_endpoint(database: str, name: str, fields: list[str]) -> Endpoint:
    return Endpoint(
        path=f"/{name}",
        method="GET",
        protocol="mongodb",
        kind=EndpointKind.READ,
        description=f"MongoDB collection {database}.{name} ({len(fields)} sampled fields)",
        response_schema={"type": "object", "properties": {f: {} for f in fields}},
        transport_meta={"kind": "collection", "database": database, "collection": name, "fields": fields},
    )
