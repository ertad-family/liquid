"""Unit tests for NDJSON + SSE parsers — both must survive chunk boundaries."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from liquid.streaming import parse_ndjson, parse_sse
from liquid.streaming.sse import SSEEvent


async def _as_chunks(data: bytes, chunk_size: int = 1) -> AsyncIterator[bytes]:
    """Replay ``data`` as N-byte chunks — exercises the split-across-chunks path."""
    for i in range(0, len(data), chunk_size):
        yield data[i : i + chunk_size]


async def _collect(ait: AsyncIterator) -> list:
    out = []
    async for item in ait:
        out.append(item)
    return out


class TestNDJSON:
    async def test_single_chunk_multiple_lines(self) -> None:
        body = b'{"id":1}\n{"id":2}\n{"id":3}\n'
        out = await _collect(parse_ndjson(_as_chunks(body, chunk_size=1024)))
        assert out == [{"id": 1}, {"id": 2}, {"id": 3}]

    async def test_byte_by_byte(self) -> None:
        body = b'{"id":1}\n{"id":2}\n'
        out = await _collect(parse_ndjson(_as_chunks(body, chunk_size=1)))
        assert out == [{"id": 1}, {"id": 2}]

    async def test_trailing_line_without_newline(self) -> None:
        body = b'{"id":1}\n{"id":2}'
        out = await _collect(parse_ndjson(_as_chunks(body, chunk_size=3)))
        assert out == [{"id": 1}, {"id": 2}]

    async def test_skips_blank_lines(self) -> None:
        body = b'\n{"a":1}\n\n\n{"b":2}\n'
        out = await _collect(parse_ndjson(_as_chunks(body, chunk_size=1)))
        assert out == [{"a": 1}, {"b": 2}]

    async def test_strict_raises_on_bad_json(self) -> None:
        body = b'{"ok":1}\nnot-json\n'
        with pytest.raises(json.JSONDecodeError):
            await _collect(parse_ndjson(_as_chunks(body)))

    async def test_non_strict_skips_bad_lines(self) -> None:
        body = b'{"ok":1}\nnot-json\n{"also":2}\n'
        out = await _collect(parse_ndjson(_as_chunks(body, chunk_size=5), strict=False))
        assert out == [{"ok": 1}, {"also": 2}]


class TestSSE:
    async def test_single_event(self) -> None:
        body = b"event: ping\ndata: hello\n\n"
        out = await _collect(parse_sse(_as_chunks(body, chunk_size=1024)))
        assert out == [SSEEvent(event="ping", data="hello")]

    async def test_default_event_type_is_message(self) -> None:
        body = b"data: plain\n\n"
        out = await _collect(parse_sse(_as_chunks(body, chunk_size=1024)))
        assert out[0].event == "message"
        assert out[0].data == "plain"

    async def test_multiline_data_joined_with_newline(self) -> None:
        body = b"data: line-a\ndata: line-b\n\n"
        out = await _collect(parse_sse(_as_chunks(body, chunk_size=1024)))
        assert out[0].data == "line-a\nline-b"

    async def test_id_and_retry_fields(self) -> None:
        body = b"id: abc-1\nretry: 5000\ndata: x\n\n"
        out = await _collect(parse_sse(_as_chunks(body, chunk_size=1)))
        assert out[0].id == "abc-1"
        assert out[0].retry == 5000

    async def test_comment_lines_ignored(self) -> None:
        body = b": heartbeat\n: keepalive\ndata: real\n\n"
        out = await _collect(parse_sse(_as_chunks(body, chunk_size=1024)))
        assert out == [SSEEvent(event="message", data="real")]

    async def test_crlf_normalised(self) -> None:
        body = b"event: ok\r\ndata: win\r\n\r\n"
        out = await _collect(parse_sse(_as_chunks(body, chunk_size=1024)))
        assert out == [SSEEvent(event="ok", data="win")]

    async def test_byte_by_byte(self) -> None:
        body = b"event: tick\ndata: 1\n\nevent: tick\ndata: 2\n\n"
        out = await _collect(parse_sse(_as_chunks(body, chunk_size=1)))
        assert len(out) == 2
        assert [ev.data for ev in out] == ["1", "2"]

    async def test_llm_token_stream(self) -> None:
        """OpenAI/Anthropic-style token stream: each chunk wraps JSON in data:."""
        body = b'data: {"delta":"Hello"}\n\ndata: {"delta":" world"}\n\ndata: [DONE]\n\n'
        events = await _collect(parse_sse(_as_chunks(body, chunk_size=7)))
        assert [ev.data for ev in events] == [
            '{"delta":"Hello"}',
            '{"delta":" world"}',
            "[DONE]",
        ]


class TestClientStreamIntegration:
    """End-to-end: Liquid.stream() through httpx.MockTransport."""

    async def test_ndjson_stream_through_client(self) -> None:
        import httpx

        from liquid.client import Liquid
        from liquid.models.adapter import AdapterConfig, SyncConfig
        from liquid.models.schema import APISchema, AuthRequirement, Endpoint

        class FakeVault:
            async def store(self, k: str, v: str) -> None: ...
            async def get(self, k: str) -> str:
                return "tok"

            async def delete(self, k: str) -> None: ...

        class FakeSink:
            async def deliver(self, records):  # type: ignore[no-untyped-def]
                return None

        class FakeLLM:
            async def chat(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                raise NotImplementedError

        def handler(request: httpx.Request) -> httpx.Response:
            body = b'{"id":1}\n{"id":2}\n{"id":3}\n'
            return httpx.Response(200, content=body, headers={"content-type": "application/x-ndjson"})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        schema = APISchema(
            source_url="https://api.example",
            service_name="x",
            discovery_method="openapi",
            endpoints=[Endpoint(path="/stream", method="GET")],
            auth=AuthRequirement(type="bearer", tier="A"),
        )
        config = AdapterConfig(schema=schema, auth_ref="r", mappings=[], sync=SyncConfig(endpoints=["/stream"]))
        liquid = Liquid(llm=FakeLLM(), vault=FakeVault(), sink=FakeSink(), http_client=client)

        stream = await liquid.stream(config, "/stream")
        out = []
        async for obj in stream:
            out.append(obj)
        await client.aclose()
        assert out == [{"id": 1}, {"id": 2}, {"id": 3}]

    async def test_sse_autodetect(self) -> None:
        import httpx

        from liquid.client import Liquid
        from liquid.models.adapter import AdapterConfig, SyncConfig
        from liquid.models.schema import APISchema, AuthRequirement, Endpoint

        class FakeVault:
            async def store(self, k: str, v: str) -> None: ...
            async def get(self, k: str) -> str:
                return "tok"

            async def delete(self, k: str) -> None: ...

        class FakeSink:
            async def deliver(self, records):  # type: ignore[no-untyped-def]
                return None

        class FakeLLM:
            async def chat(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                raise NotImplementedError

        def handler(request: httpx.Request) -> httpx.Response:
            body = b'data: {"t":"a"}\n\ndata: {"t":"b"}\n\n'
            return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        schema = APISchema(
            source_url="https://api.example",
            service_name="x",
            discovery_method="openapi",
            endpoints=[Endpoint(path="/events", method="GET")],
            auth=AuthRequirement(type="bearer", tier="A"),
        )
        config = AdapterConfig(schema=schema, auth_ref="r", mappings=[], sync=SyncConfig(endpoints=["/events"]))
        liquid = Liquid(llm=FakeLLM(), vault=FakeVault(), sink=FakeSink(), http_client=client)

        stream = await liquid.stream(config, "/events")
        out = []
        async for ev in stream:
            out.append(ev)
        await client.aclose()
        assert len(out) == 2
        assert [ev.data for ev in out] == ['{"t":"a"}', '{"t":"b"}']
