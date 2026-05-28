"""A2A transport driver — invoke an agent's skill via JSON-RPC.

The A2A protocol sends tasks/messages to an agent's endpoint as JSON-RPC 2.0
calls. The current spec uses ``message/send`` (an older draft used ``tasks/send``
— we try the modern one first and fall back if the agent reports the method as
unknown). The agent answers with a task object containing artifacts (text /
structured) and an optional message — both flattened into records here.

A2A rides plain HTTP, so the shared httpx client / SSRF guard / bearer auth from
``ctx.headers`` all apply, exactly as for GraphQL and SOAP.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from liquid.transport.base import DriverResponse, FetchContext

logger = logging.getLogger(__name__)

_PRIMARY_METHOD = "message/send"
_FALLBACK_METHOD = "tasks/send"


class A2ADriver:
    scheme = "a2a"

    async def fetch(self, ctx: FetchContext) -> DriverResponse:
        assert ctx.http_client is not None, "A2A driver requires an http_client"
        meta = ctx.endpoint.transport_meta or {}
        agent_url = meta.get("agent_url") or ctx.base_url
        skill_id = meta.get("skill_id") or ""
        params = ctx.params or {}

        rpc_request = _build_rpc(_PRIMARY_METHOD, skill_id, params)
        response = await ctx.http_client.post(
            agent_url, json=rpc_request, headers=ctx.headers, auth=ctx.auth, follow_redirects=True
        )

        # If the agent doesn't know message/send, retry with the older tasks/send.
        if response.is_success and _is_method_not_found(_parse_json(response)):
            rpc_request = _build_rpc(_FALLBACK_METHOD, skill_id, params)
            response = await ctx.http_client.post(
                agent_url, json=rpc_request, headers=ctx.headers, auth=ctx.auth, follow_redirects=True
            )

        resp_headers = dict(response.headers)
        if not response.is_success:
            return DriverResponse(
                status_code=response.status_code,
                headers=resp_headers,
                error_body=response.text[:500],
                raw=response,
            )

        body = _parse_json(response) or {}
        if "error" in body:
            err = body["error"] or {}
            return DriverResponse(
                status_code=422,
                headers=resp_headers,
                error_body=f"A2A {err.get('code', '?')}: {err.get('message', err)}"[:500],
            )

        return DriverResponse(
            status_code=response.status_code,
            headers=resp_headers,
            records=_extract_records(body.get("result")),
        )


def _build_rpc(method: str, skill_id: str, params: dict) -> dict:
    """Wrap caller params as an A2A message JSON-RPC envelope."""
    # The caller can pass a pre-formed `message` dict; otherwise we build one
    # from `message`/`text` keys (most common) and forward the rest as metadata.
    message_field = params.get("message")
    if isinstance(message_field, dict):
        message = message_field
    else:
        text = message_field if isinstance(message_field, str) else params.get("text", "")
        message = {
            "role": "user",
            "parts": [{"type": "text", "text": str(text)}] if text else [],
        }
    rpc_params: dict[str, Any] = {"message": message}
    metadata = {k: v for k, v in params.items() if k not in ("message", "text")}
    if skill_id:
        metadata.setdefault("skill", skill_id)
    if metadata:
        rpc_params["metadata"] = metadata
    return {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method, "params": rpc_params}


def _is_method_not_found(body: dict | None) -> bool:
    if not body or "error" not in body:
        return False
    # JSON-RPC standard "method not found" is -32601.
    return (body["error"] or {}).get("code") == -32601


def _parse_json(response: Any) -> dict | None:
    try:
        return response.json()
    except (ValueError, AttributeError):
        return None


def _extract_records(result: Any) -> list[dict]:
    """Pull records out of an A2A task result.

    The result usually carries ``artifacts`` (the agent's outputs) and may include
    a final ``message``. We surface artifact parts as records, falling back to
    the message text or the raw result so the caller never gets nothing.
    """
    if result is None:
        return []
    if not isinstance(result, dict):
        return [{"value": result}]

    records: list[dict] = []
    for artifact in result.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        for part in artifact.get("parts") or []:
            rec = _part_to_record(part)
            if rec is not None:
                records.append(rec)
    if records:
        return records

    message = result.get("message") or {}
    for part in (message.get("parts") or []) if isinstance(message, dict) else []:
        rec = _part_to_record(part)
        if rec is not None:
            records.append(rec)
    if records:
        return records

    # Last resort: hand back the whole result so the caller can introspect it.
    return [result]


def _part_to_record(part: Any) -> dict | None:
    if not isinstance(part, dict):
        return None
    if "data" in part and isinstance(part["data"], dict | list):
        data = part["data"]
        if isinstance(data, list):
            return {"items": data}
        return data
    text = part.get("text")
    if isinstance(text, str):
        return {"text": text}
    file_ref = part.get("file") or part.get("fileUri")
    if file_ref:
        return {"file": file_ref}
    return None
