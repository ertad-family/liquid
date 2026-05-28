"""A2A driver: JSON-RPC ``message/send`` to the agent's URL, with a fallback to
the older ``tasks/send`` if the agent reports the modern method as unknown.
Records come out of artifact parts or the agent's reply message."""

import httpx
import pytest

from liquid.exceptions import SyncRuntimeError, VaultError
from liquid.models.schema import Endpoint
from liquid.sync.fetcher import Fetcher
from liquid.transport.a2a import _build_rpc, _extract_records


class FakeVault:
    async def store(self, key, value):
        pass

    async def get(self, key):
        raise VaultError(key)

    async def delete(self, key):
        pass


META = {"agent_url": "https://agent.example.com/a2a", "skill_id": "translate", "skill_name": "Translate"}


def _endpoint() -> Endpoint:
    return Endpoint(path="/a2a/skills/translate", protocol="a2a", method="POST", transport_meta=META)


def test_build_rpc_wraps_text_in_message():
    rpc = _build_rpc("message/send", "translate", {"text": "hello"})
    assert rpc["jsonrpc"] == "2.0"
    assert rpc["method"] == "message/send"
    msg = rpc["params"]["message"]
    assert msg["role"] == "user"
    assert msg["parts"] == [{"type": "text", "text": "hello"}]
    assert rpc["params"]["metadata"]["skill"] == "translate"


def test_build_rpc_passthrough_message_dict():
    custom = {"role": "user", "parts": [{"type": "data", "data": {"x": 1}}]}
    rpc = _build_rpc("message/send", "s", {"message": custom, "lang": "fr"})
    assert rpc["params"]["message"] == custom
    assert rpc["params"]["metadata"]["lang"] == "fr"


def test_extract_records_from_artifact_parts():
    result = {
        "artifacts": [
            {"parts": [{"type": "text", "text": "Hello"}, {"type": "data", "data": {"k": "v"}}]},
            {"parts": [{"type": "text", "text": "World"}]},
        ]
    }
    assert _extract_records(result) == [{"text": "Hello"}, {"k": "v"}, {"text": "World"}]


def test_extract_records_falls_back_to_message():
    result = {"message": {"parts": [{"type": "text", "text": "no artifacts here"}]}}
    assert _extract_records(result) == [{"text": "no artifacts here"}]


async def _run(handler, *, extra_params=None):
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        fetcher = Fetcher(http_client=client, vault=FakeVault())
        return await fetcher.fetch(
            endpoint=_endpoint(),
            base_url="https://agent.example.com",
            auth_ref="none",
            extra_params=extra_params,
        )


async def test_driver_calls_message_send_and_extracts():
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        assert str(req.url) == "https://agent.example.com/a2a"
        seen["body"] = req.read()
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": "x",
                "result": {"artifacts": [{"parts": [{"type": "text", "text": "Bonjour"}]}]},
            },
        )

    result = await _run(handler, extra_params={"text": "hello"})
    assert result.records == [{"text": "Bonjour"}]
    assert b'"method":"message/send"' in seen["body"].replace(b" ", b"")
    assert b'"hello"' in seen["body"]


async def test_driver_falls_back_to_tasks_send_on_method_not_found():
    seen: list[bytes] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.read())
        if b'"message/send"' in seen[-1]:
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": "x", "error": {"code": -32601, "message": "method not found"}}
            )
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": "x",
                "result": {"artifacts": [{"parts": [{"type": "text", "text": "ok"}]}]},
            },
        )

    result = await _run(handler, extra_params={"text": "go"})
    assert result.records == [{"text": "ok"}]
    assert len(seen) == 2  # one for message/send, one for tasks/send
    assert b'"tasks/send"' in seen[-1]


async def test_driver_surfaces_rpc_error_as_failure():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": "x", "error": {"code": -32603, "message": "Internal error"}},
        )

    with pytest.raises(SyncRuntimeError):
        await _run(handler, extra_params={"text": "x"})
