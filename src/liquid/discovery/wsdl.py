"""SOAP/WSDL discovery.

Fetches a WSDL document (the URL as given, or with ``?wsdl``) and turns each
SOAP operation into an :class:`Endpoint` the :class:`~liquid.transport.soap`
driver can invoke. We parse with the stdlib :mod:`xml.etree.ElementTree` — no
extra dependency — and match elements by *local* name so the many WSDL/SOAP
namespace prefixes (``wsdl:``, ``soap:``, ``soap12:``, ``s:`` …) don't matter.

WSDL is verbose; we capture exactly what's needed to build a request envelope:
the service endpoint URL, the SOAP version, and per operation its ``soapAction``,
the request wrapper element (name + namespace), and its parameter names. Deep
XSD typing is intentionally out of scope — parameter values are rendered as
text, which covers the overwhelming majority of document/literal services.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

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

_WSDL_NS = "http://schemas.xmlsoap.org/wsdl/"
_SOAP11_BIND = "http://schemas.xmlsoap.org/wsdl/soap/"
_SOAP12_BIND = "http://schemas.xmlsoap.org/wsdl/soap12/"


def _local(tag: str) -> str:
    """Strip the ``{namespace}`` prefix ElementTree prepends to a tag."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _findall_local(root: ET.Element, name: str) -> list[ET.Element]:
    return [el for el in root.iter() if _local(el.tag) == name]


def _qname_local(qname: str | None) -> str:
    """Local part of a ``prefix:Name`` QName reference."""
    if not qname:
        return ""
    return qname.rsplit(":", 1)[-1]


class WSDLDiscovery:
    """Discovers SOAP services by parsing their WSDL."""

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._external_client = http_client

    async def discover(self, url: str) -> APISchema | None:
        from liquid.discovery.utils import managed_http_client

        async with managed_http_client(self._external_client) as client:
            text = await self._fetch_wsdl(client, url)
            if text is None:
                return None
            try:
                root = ET.fromstring(text)
            except ET.ParseError:
                return None
            if _local(root.tag) != "definitions":
                return None
            return self._parse(root, url)

    async def _fetch_wsdl(self, client: httpx.AsyncClient, url: str) -> str | None:
        # Try the URL as given first (it may already point at the WSDL), then the
        # conventional ``?wsdl`` query a SOAP endpoint serves its contract on.
        candidates = [url]
        if "?" not in url:
            candidates.append(f"{url}?wsdl")
        for candidate in candidates:
            try:
                resp = await client.get(candidate, timeout=10.0, follow_redirects=True)
            except Exception:
                continue
            if not resp.is_success:
                continue
            body = resp.text
            if "<definitions" in body or "wsdl:definitions" in body:
                logger.info("WSDL discovered at %s", candidate)
                return body
        return None

    def _parse(self, root: ET.Element, source_url: str) -> APISchema:
        target_ns = root.get("targetNamespace", "")
        soap_version = self._detect_soap(root)
        endpoint_url = self._service_location(root) or source_url

        # message name -> request wrapper element local name
        msg_to_element: dict[str, str] = {}
        for msg in _findall_local(root, "message"):
            if _local(msg.tag) != "message":
                continue
            parts = [p for p in msg if _local(p.tag) == "part"]
            if parts:
                ref = parts[0].get("element") or parts[0].get("type")
                msg_to_element[msg.get("name", "")] = _qname_local(ref)

        # operation name -> input message name (from portType)
        op_to_input: dict[str, str] = {}
        for pt in root:
            if _local(pt.tag) != "portType":
                continue
            for op in pt:
                if _local(op.tag) != "operation":
                    continue
                inp = next((c for c in op if _local(c.tag) == "input"), None)
                if inp is not None:
                    op_to_input[op.get("name", "")] = _qname_local(inp.get("message"))

        # soapAction per operation (from the binding)
        actions = self._soap_actions(root)

        # request element -> parameter names (from the inline XSD)
        element_params = self._element_params(root)

        endpoints: list[Endpoint] = []
        seen: set[str] = set()
        for op_name in op_to_input:
            if op_name in seen:
                continue
            seen.add(op_name)
            input_msg = op_to_input.get(op_name, "")
            req_element = msg_to_element.get(input_msg) or op_name
            params = element_params.get(req_element, [])
            endpoints.append(
                Endpoint(
                    path=f"/soap#{op_name}",
                    method="POST",
                    protocol="soap",
                    kind=EndpointKind.READ,
                    parameters=[Parameter(name=p, location=ParameterLocation.BODY, required=False) for p in params],
                    transport_meta={
                        "soap_version": soap_version,
                        "endpoint": endpoint_url,
                        "soap_action": actions.get(op_name, ""),
                        "request_element": req_element,
                        "request_namespace": target_ns,
                        "param_names": params,
                    },
                )
            )

        return APISchema(
            source_url=source_url,
            service_name=self._infer_service_name(root, source_url),
            discovery_method="soap",
            endpoints=endpoints,
            auth=AuthRequirement(type="custom", tier="B"),
        )

    def _detect_soap(self, root: ET.Element) -> str:
        for el in root.iter():
            if _local(el.tag) == "binding" and el.tag.startswith("{"):
                ns = el.tag[1:].split("}", 1)[0]
                if ns == _SOAP12_BIND:
                    return "1.2"
                if ns == _SOAP11_BIND:
                    return "1.1"
        return "1.1"

    def _service_location(self, root: ET.Element) -> str | None:
        for el in root.iter():
            if _local(el.tag) == "address" and el.get("location"):
                return el.get("location")
        return None

    def _soap_actions(self, root: ET.Element) -> dict[str, str]:
        actions: dict[str, str] = {}
        for binding in root:
            if _local(binding.tag) != "binding":
                continue
            for op in binding:
                if _local(op.tag) != "operation":
                    continue
                name = op.get("name", "")
                soap_op = next((c for c in op if _local(c.tag) == "operation"), None)
                if soap_op is not None:
                    actions[name] = soap_op.get("soapAction", "") or ""
        return actions

    def _element_params(self, root: ET.Element) -> dict[str, list[str]]:
        """Map each top-level XSD element to its immediate child element names."""
        out: dict[str, list[str]] = {}
        for el in root.iter():
            if _local(el.tag) != "element" or not el.get("name"):
                continue
            # Find a nested sequence/all and collect its child element names.
            child_names: list[str] = []
            for sub in el.iter():
                if sub is el:
                    continue
                if _local(sub.tag) == "element" and sub.get("name"):
                    child_names.append(sub.get("name"))
            if child_names:
                out[el.get("name")] = child_names
        return out

    @staticmethod
    def _infer_service_name(root: ET.Element, url: str) -> str:
        for el in root.iter():
            if _local(el.tag) == "service" and el.get("name"):
                return el.get("name")
        from liquid.discovery.utils import infer_service_name

        return infer_service_name(url)
