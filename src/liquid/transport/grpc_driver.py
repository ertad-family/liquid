"""gRPC transport driver.

Re-uses server reflection (cached per target) to build the protobuf request and
response message classes, fills the request from the caller's params, invokes the
method over a ``grpc.aio`` channel, and converts the response message(s) to dicts.
Both unary-unary and unary-server-streaming methods are supported; streaming is
bounded. gRPC status codes are mapped onto HTTP-like codes so the Fetcher's
shared error mapping applies (UNAUTHENTICATED→401, NOT_FOUND→404, …).

Descriptors aren't JSON-serializable, so they're not stored on the adapter; the
driver reflects lazily at fetch time and caches the descriptor pool per target.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from liquid.transport.base import DriverResponse, FetchContext

logger = logging.getLogger(__name__)

_MAX_STREAM_RECORDS = 1000

# target -> (sync reflection channel, descriptor pool). The pool fetches
# descriptors lazily over the channel, so we keep both alive for the process.
_POOLS: dict[tuple[str, bool], tuple[Any, Any]] = {}
_POOLS_LOCK = threading.Lock()


class GRPCDriver:
    scheme = "grpc"

    async def fetch(self, ctx: FetchContext) -> DriverResponse:
        import grpc
        from google.protobuf import json_format

        meta = ctx.endpoint.transport_meta or {}
        target = meta["target"]
        secure = bool(meta["secure"])
        server_streaming = bool(meta.get("server_streaming"))

        req_bytes, resp_cls = await asyncio.to_thread(
            _build_request, target, secure, meta["input_type"], meta["output_type"], ctx.params
        )
        metadata = await _auth_metadata(ctx)

        channel = (
            grpc.aio.secure_channel(target, grpc.ssl_channel_credentials())
            if secure
            else grpc.aio.insecure_channel(target)
        )
        try:
            if server_streaming:
                method = channel.unary_stream(
                    meta["grpc_path"], request_serializer=_identity, response_deserializer=resp_cls.FromString
                )
                records: list[dict] = []
                async for resp in method(req_bytes, metadata=metadata, timeout=60.0):
                    records.append(json_format.MessageToDict(resp, preserving_proto_field_name=True))
                    if len(records) >= _MAX_STREAM_RECORDS:
                        break
            else:
                method = channel.unary_unary(
                    meta["grpc_path"], request_serializer=_identity, response_deserializer=resp_cls.FromString
                )
                resp = await method(req_bytes, metadata=metadata, timeout=60.0)
                records = [json_format.MessageToDict(resp, preserving_proto_field_name=True)]
            return DriverResponse(status_code=200, records=records)
        except grpc.aio.AioRpcError as e:
            return DriverResponse(
                status_code=_status_to_http(e.code()),
                error_body=(e.details() or str(e.code()))[:500],
            )
        finally:
            await channel.close()


def _identity(payload: bytes) -> bytes:
    return payload


def _get_pool(target: str, secure: bool) -> tuple[Any, Any]:
    import grpc
    from google.protobuf.descriptor_pool import DescriptorPool
    from grpc_reflection.v1alpha.proto_reflection_descriptor_database import ProtoReflectionDescriptorDatabase

    key = (target, secure)
    with _POOLS_LOCK:
        cached = _POOLS.get(key)
        if cached is not None:
            return cached
        channel = (
            grpc.secure_channel(target, grpc.ssl_channel_credentials()) if secure else grpc.insecure_channel(target)
        )
        pool = DescriptorPool(ProtoReflectionDescriptorDatabase(channel))
        _POOLS[key] = (channel, pool)
        return channel, pool


def _build_request(target: str, secure: bool, input_type: str, output_type: str, params: dict | None):
    """Build the serialized request + response class (runs in a thread — reflection blocks)."""
    from google.protobuf import json_format, message_factory

    _, pool = _get_pool(target, secure)
    req_cls = message_factory.GetMessageClass(pool.FindMessageTypeByName(input_type))
    resp_cls = message_factory.GetMessageClass(pool.FindMessageTypeByName(output_type))
    req = req_cls()
    if params:
        json_format.ParseDict(params, req, ignore_unknown_fields=True)
    return req.SerializeToString(), resp_cls


async def _auth_metadata(ctx: FetchContext) -> list[tuple[str, str]]:
    """Send a stored token as gRPC ``authorization`` metadata, if present."""
    # Auth is best-effort — a missing/failed token lookup means an unauthenticated call.
    try:
        token = await ctx.vault.get(ctx.auth_ref)
    except Exception:
        return []
    return [("authorization", f"Bearer {token}")] if token else []


def _status_to_http(code: Any) -> int:
    import grpc

    return {
        grpc.StatusCode.OK: 200,
        grpc.StatusCode.INVALID_ARGUMENT: 400,
        grpc.StatusCode.UNAUTHENTICATED: 401,
        grpc.StatusCode.PERMISSION_DENIED: 403,
        grpc.StatusCode.NOT_FOUND: 404,
        grpc.StatusCode.UNIMPLEMENTED: 404,
        grpc.StatusCode.RESOURCE_EXHAUSTED: 429,
        grpc.StatusCode.UNAVAILABLE: 503,
        grpc.StatusCode.DEADLINE_EXCEEDED: 504,
    }.get(code, 500)
