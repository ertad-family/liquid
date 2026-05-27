"""gRPC discovery via server reflection.

Triggers only for ``grpc://`` / ``grpcs://`` targets. Uses the gRPC server
reflection service to enumerate services and methods and to fetch their protobuf
descriptors, turning each unary or server-streaming method into an
:class:`Endpoint` the gRPC driver can invoke. Client-streaming and bidi methods
are skipped — there's no single-shot request shape to drive them from a fetch.

``grpcio`` is an optional dependency (the ``grpc`` extra). Without it, discovery
quietly returns ``None`` so the rest of the pipeline is unaffected.
"""

from __future__ import annotations

import asyncio
import logging

from liquid.exceptions import DiscoveryError
from liquid.models.schema import (
    APISchema,
    AuthRequirement,
    Endpoint,
    EndpointKind,
    Parameter,
    ParameterLocation,
)

logger = logging.getLogger(__name__)

# Reflection / health services aren't part of the user's API surface.
_INTERNAL_SERVICE_PREFIXES = ("grpc.reflection.", "grpc.health.", "grpc.channelz.")


def parse_grpc_target(url: str) -> tuple[str, bool] | None:
    """Return ``(host:port, secure)`` for a gRPC URL, or ``None`` if not gRPC."""
    if url.startswith("grpcs://"):
        return url[len("grpcs://") :].rstrip("/"), True
    if url.startswith("grpc://"):
        return url[len("grpc://") :].rstrip("/"), False
    return None


class GRPCDiscovery:
    """Discovers gRPC services through server reflection."""

    async def discover(self, url: str) -> APISchema | None:
        parsed = parse_grpc_target(url)
        if parsed is None:
            return None
        try:
            import grpc  # noqa: F401
        except ImportError:
            logger.warning("gRPC URL given but grpcio is not installed (pip install 'liquid-api[grpc]')")
            return None

        target, secure = parsed
        try:
            methods = await asyncio.to_thread(self._reflect, target, secure)
        except Exception as e:
            raise DiscoveryError(f"gRPC reflection failed for {target}: {e}") from e

        if not methods:
            return None

        endpoints = [
            Endpoint(
                path=f"/grpc#{m['service']}/{m['method']}",
                method="POST",
                protocol="grpc",
                kind=EndpointKind.READ,
                description=f"{m['service']}/{m['method']}",
                parameters=[
                    Parameter(name=f, location=ParameterLocation.BODY, required=False) for f in m["input_fields"]
                ],
                transport_meta={
                    "target": target,
                    "secure": secure,
                    "service": m["service"],
                    "method": m["method"],
                    "grpc_path": m["path"],
                    "input_type": m["input_type"],
                    "output_type": m["output_type"],
                    "server_streaming": m["server_streaming"],
                },
            )
            for m in methods
        ]

        return APISchema(
            source_url=url,
            service_name=methods[0]["service"].rsplit(".", 1)[-1],
            discovery_method="grpc",
            endpoints=endpoints,
            auth=AuthRequirement(type="bearer", tier="A"),
        )

    def _reflect(self, target: str, secure: bool) -> list[dict]:
        """Synchronous reflection (run in a thread): enumerate methods + descriptors."""
        import grpc
        from google.protobuf.descriptor_pool import DescriptorPool
        from grpc_reflection.v1alpha.proto_reflection_descriptor_database import ProtoReflectionDescriptorDatabase

        channel = (
            grpc.secure_channel(target, grpc.ssl_channel_credentials()) if secure else grpc.insecure_channel(target)
        )
        try:
            db = ProtoReflectionDescriptorDatabase(channel)
            pool = DescriptorPool(db)
            out: list[dict] = []
            for service_name in db.get_services():
                if service_name.startswith(_INTERNAL_SERVICE_PREFIXES):
                    continue
                service_desc = pool.FindServiceByName(service_name)
                for method in service_desc.methods:
                    if method.client_streaming:
                        continue  # no single-shot request to drive client/bidi streaming
                    out.append(
                        {
                            "service": service_name,
                            "method": method.name,
                            "path": f"/{service_name}/{method.name}",
                            "input_type": method.input_type.full_name,
                            "output_type": method.output_type.full_name,
                            "server_streaming": method.server_streaming,
                            "input_fields": [f.name for f in method.input_type.fields],
                        }
                    )
            return out
        finally:
            channel.close()
