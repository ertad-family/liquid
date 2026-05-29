"""Telegram connector — human as a node. Verified in-process with
httpx.MockTransport (Bot API responses faked); no live bot/token needed."""

from __future__ import annotations

import json

import httpx

from liquid.connectors import TelegramConnector
from liquid.sense_loop import react

_TOKEN = "123:ABC"


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _update(update_id: int, chat_id: int, text: str) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "from": {"id": chat_id, "username": "alice"},
            "chat": {"id": chat_id, "type": "private"},
            "text": text,
        },
    }


async def test_me_verifies_token():
    def handler(request):
        assert request.url.path == f"/bot{_TOKEN}/getMe"
        return httpx.Response(200, json={"ok": True, "result": {"id": 7, "is_bot": True, "username": "echo_bot"}})

    async with _client(handler) as c:
        tg = TelegramConnector(_TOKEN, http_client=c)
        me = await tg.me()
    assert me["result"]["username"] == "echo_bot"


async def test_send_posts_sendmessage():
    captured = {}

    def handler(request):
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 99}})

    async with _client(handler) as c:
        tg = TelegramConnector(_TOKEN, http_client=c)
        res = await tg.send(555, "hello", reply_to_message_id=42)

    assert captured["path"] == f"/bot{_TOKEN}/sendMessage"
    assert captured["body"] == {"chat_id": 555, "text": "hello", "reply_to_message_id": 42}
    assert res["result"]["message_id"] == 99


async def test_api_error_raises():
    def handler(request):
        return httpx.Response(200, json={"ok": False, "description": "chat not found"})

    async with _client(handler) as c:
        tg = TelegramConnector(_TOKEN, http_client=c)
        try:
            await tg.send(1, "x")
            raise AssertionError("expected an error")
        except httpx.HTTPError as e:
            assert "chat not found" in str(e)


async def test_sense_yields_messages_with_resumable_cursor():
    batches = [
        [_update(10, 555, "hi"), _update(11, 555, "there")],
        [],  # subsequent polls are empty → stream ends on max_events before this matters
    ]
    seen_offsets = []

    def handler(request):
        offset = request.url.params.get("offset")
        seen_offsets.append(offset)
        batch = batches.pop(0) if batches else []
        return httpx.Response(200, json={"ok": True, "result": batch})

    async with _client(handler) as c:
        tg = TelegramConnector(_TOKEN, http_client=c)
        events = [e async for e in tg.sense(max_events=2, max_seconds=5, long_poll=0)]

    assert [e.payload["text"] for e in events] == ["hi", "there"]
    assert [e.payload["chat_id"] for e in events] == [555, 555]
    assert all(e.modality == "message" and e.source == "telegram" for e in events)
    assert events[-1].cursor == "11"  # resumable: last update_id


async def test_sense_acks_with_offset_on_next_poll():
    # After the first batch (ids 10,11), the next getUpdates must carry offset=12.
    polls = []
    first = [_update(10, 1, "a"), _update(11, 1, "b")]

    def handler(request):
        polls.append(request.url.params.get("offset"))
        # First poll returns the batch; later polls are empty.
        return httpx.Response(200, json={"ok": True, "result": first if len(polls) == 1 else []})

    async with _client(handler) as c:
        tg = TelegramConnector(_TOKEN, http_client=c)
        # Bound by time so we get at least a second poll after draining the batch.
        _ = [e async for e in tg.sense(max_seconds=0.3, long_poll=0)]

    assert polls[0] is None  # first poll has no offset
    assert "12" in polls[1:]  # a later poll acked past update_id 11


async def test_sense_composes_with_react():
    replies = []

    def handler(request):
        if request.url.path.endswith("/getUpdates"):
            if request.url.params.get("offset") is None:
                return httpx.Response(200, json={"ok": True, "result": [_update(1, 9, "ping")]})
            return httpx.Response(200, json={"ok": True, "result": []})
        # sendMessage
        replies.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    async with _client(handler) as c:
        tg = TelegramConnector(_TOKEN, http_client=c)

        async def echo(event):
            await tg.send(event.payload["chat_id"], f"echo: {event.payload['text']}")

        count = await react(tg.sense(max_events=1, max_seconds=5, long_poll=0), echo)

    assert count == 1
    assert replies == [{"chat_id": 9, "text": "echo: ping"}]
