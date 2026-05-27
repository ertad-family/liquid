"""SOAP transport driver.

Builds a SOAP envelope from the operation metadata WSDL discovery captured,
POSTs it to the service endpoint with the right ``SOAPAction`` / content type for
the SOAP version, then parses the XML response into record dicts (namespaces
stripped). SOAP Faults — whether returned with HTTP 500 (1.1) or 200 — surface
as fetch failures.

Like GraphQL, SOAP rides HTTP, so the shared httpx client / SSRF guard / auth
all apply. The request is posted to the WSDL's ``soap:address`` location (which
can differ from the WSDL URL), taken from ``Endpoint.transport_meta['endpoint']``.
"""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from liquid.transport.base import DriverResponse, FetchContext

_ENV_NS = {
    "1.1": "http://schemas.xmlsoap.org/soap/envelope/",
    "1.2": "http://www.w3.org/2003/05/soap-envelope",
}


class SOAPDriver:
    scheme = "soap"

    async def fetch(self, ctx: FetchContext) -> DriverResponse:
        assert ctx.http_client is not None, "SOAP driver requires an http_client"
        meta = ctx.endpoint.transport_meta or {}
        version = meta.get("soap_version", "1.1")
        endpoint = meta.get("endpoint") or ctx.base_url
        action = meta.get("soap_action", "")

        envelope = _build_envelope(meta, ctx.params, version)
        headers = dict(ctx.headers)
        if version == "1.2":
            headers["Content-Type"] = f'application/soap+xml; charset=utf-8; action="{action}"'
        else:
            headers["Content-Type"] = "text/xml; charset=utf-8"
            headers["SOAPAction"] = f'"{action}"'

        response = await ctx.http_client.post(
            endpoint,
            content=envelope.encode("utf-8"),
            headers=headers,
            auth=ctx.auth,
            follow_redirects=True,
        )
        resp_headers = dict(response.headers)

        if not response.is_success:
            # SOAP 1.1 faults arrive as HTTP 500 — surface via the raw response so
            # the Fetcher maps the status; the body carries the fault detail.
            return DriverResponse(
                status_code=response.status_code,
                headers=resp_headers,
                error_body=response.text[:500],
                raw=response,
            )

        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as e:
            return DriverResponse(status_code=502, headers=resp_headers, error_body=f"Malformed XML: {e}")

        body = _find_local(root, "Body")
        if body is None:
            return DriverResponse(status_code=502, headers=resp_headers, error_body="No SOAP Body in response")

        fault = _find_local(body, "Fault")
        if fault is not None:
            return DriverResponse(status_code=500, headers=resp_headers, error_body=_text(fault)[:500])

        wrapper = next(iter(body), None)
        if wrapper is None:
            return DriverResponse(status_code=response.status_code, headers=resp_headers, records=[])

        return DriverResponse(
            status_code=response.status_code,
            headers=resp_headers,
            records=_to_records(_elem_to_obj(wrapper)),
        )


def _build_envelope(meta: dict, params: dict | None, version: str) -> str:
    env_ns = _ENV_NS.get(version, _ENV_NS["1.1"])
    element = meta.get("request_element", "")
    ns = meta.get("request_namespace", "")
    body_inner = "".join(f"<tns:{k}>{escape(str(v))}</tns:{k}>" for k, v in (params or {}).items() if v is not None)
    return (
        f'<?xml version="1.0" encoding="utf-8"?>'
        f'<soapenv:Envelope xmlns:soapenv="{env_ns}">'
        f"<soapenv:Body>"
        f'<tns:{element} xmlns:tns="{ns}">{body_inner}</tns:{element}>'
        f"</soapenv:Body>"
        f"</soapenv:Envelope>"
    )


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _find_local(parent: ET.Element, name: str) -> ET.Element | None:
    for el in parent.iter():
        if _local(el.tag) == name:
            return el
    return None


def _text(el: ET.Element) -> str:
    return "".join(el.itertext()).strip()


def _elem_to_obj(el: ET.Element) -> Any:
    """Convert an XML element to a nested dict/list/scalar (namespaces stripped)."""
    children = list(el)
    if not children:
        return (el.text or "").strip() or None
    obj: dict[str, Any] = {}
    for child in children:
        tag = _local(child.tag)
        value = _elem_to_obj(child)
        if tag in obj:
            if not isinstance(obj[tag], list):
                obj[tag] = [obj[tag]]
            obj[tag].append(value)
        else:
            obj[tag] = value
    return obj


def _to_records(result: Any) -> list[dict]:
    """Flatten a SOAP response wrapper into records, unwrapping result envelopes.

    Typical shapes: ``{OpResult: {...}}``, ``{OpResult: [{...}, {...}]}`` or a
    nested ``{OpResult: {Items: [...]}}``. We peel one or two single-key layers
    to land on the actual record list/object, mirroring the REST envelope logic.
    """
    if result is None:
        return []
    if not isinstance(result, dict):
        return [{"value": result}]
    current: Any = result
    for _ in range(2):
        if isinstance(current, dict) and len(current) == 1:
            inner = next(iter(current.values()))
            if isinstance(inner, list):
                return [r if isinstance(r, dict) else {"value": r} for r in inner]
            if isinstance(inner, dict):
                current = inner
                continue
        break
    if isinstance(current, list):
        return [r if isinstance(r, dict) else {"value": r} for r in current]
    return [current] if isinstance(current, dict) else [{"value": current}]
