"""Telegram as a node — the agent perceives a human and acts back.

A Telegram bot is the simplest "human as a node": no approval, a free Bot API,
plain HTTP+JSON. :meth:`TelegramConnector.sense` long-polls ``getUpdates`` and
yields each incoming message as a ``modality="message"``
:class:`~liquid.transport.SenseEvent` (resumable by ``update_id``); the stream
composes with :func:`liquid.react` / :func:`liquid.merge_senses` like any other
sense. :meth:`~TelegramConnector.send` is the hands — ``sendMessage`` back to a
chat. So an agent can *perceive a person and answer them* through the same
afferent/efferent shape it uses for databases and APIs.

httpx only (a core dependency); the bot token is supplied by the caller (pull it
from a vault / env — the connector never persists it).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

from liquid.transport.base import SenseEvent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org"
# Sub-objects an update may carry; the first present is the perceived signal.
_UPDATE_KINDS = ("message", "edited_message", "channel_post", "edited_channel_post", "callback_query")


class TelegramConnector:
    """Sense and act on a human over the Telegram Bot API.

    ``token`` is a bot token from @BotFather. Pass a shared ``http_client`` to
    reuse a connection pool (and SSRF guard); otherwise one is created per call.
    ``api_base`` is overridable for a local Bot API server.
    """

    def __init__(
        self,
        token: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        api_base: str = _API_BASE,
    ) -> None:
        self._token = token
        self._client = http_client
        self._base = f"{api_base.rstrip('/')}/bot{token}"

    async def me(self) -> dict[str, Any]:
        """``getMe`` — verify the token and return the bot's identity."""
        return await self._call("getMe", method="GET")

    async def send(self, chat_id: int | str, text: str, **kwargs: Any) -> dict[str, Any]:
        """``sendMessage`` — the hands: reply to a chat. Extra kwargs pass through
        (e.g. ``reply_to_message_id``, ``parse_mode``, ``reply_markup``)."""
        payload = {"chat_id": chat_id, "text": text, **kwargs}
        return await self._call("sendMessage", json=payload)

    async def sense(
        self,
        *,
        offset: int | None = None,
        long_poll: float = 25.0,
        allowed_updates: list[str] | None = None,
        max_events: int | None = None,
        max_seconds: float | None = None,
    ) -> AsyncIterator[SenseEvent]:
        """Perceive incoming Telegram messages as a live event stream.

        Long-polls ``getUpdates`` (the server holds each request up to
        ``long_poll`` seconds when idle, so this is push-like, not busy-polling)
        and yields one ``modality="message"`` event per update, with the
        ``update_id`` as a resumable ``cursor`` — pass it back as ``offset`` (the
        last id ``+ 1`` is acked automatically). Bounded by ``max_events`` /
        ``max_seconds``; network/JSON errors end the stream quietly.

        ```python
        async for event in tg.sense(max_events=10):
            await tg.send(event.payload["chat_id"], f"echo: {event.payload['text']}")
        ```
        """
        import asyncio

        next_offset = offset
        emitted = 0
        loop = asyncio.get_running_loop()
        deadline = (loop.time() + max_seconds) if max_seconds is not None else None

        async with _ClientHolder(self._client) as client:
            while True:
                if deadline is not None:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        return
                    poll = max(0.0, min(long_poll, remaining))
                else:
                    poll = long_poll
                params: dict[str, Any] = {"timeout": int(poll)}
                if next_offset is not None:
                    params["offset"] = next_offset
                if allowed_updates is not None:
                    params["allowed_updates"] = allowed_updates
                try:
                    data = await self._call("getUpdates", method="GET", params=params, client=client, timeout=poll + 10)
                except (httpx.HTTPError, ValueError):
                    return
                for update in data.get("result", []) if isinstance(data, dict) else []:
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        next_offset = update_id + 1
                    yield SenseEvent(
                        source="telegram",
                        modality="message",
                        payload=_normalize_update(update),
                        cursor=str(update_id) if update_id is not None else None,
                    )
                    emitted += 1
                    if max_events is not None and emitted >= max_events:
                        return
                if deadline is not None and loop.time() >= deadline:
                    return

    async def _call(
        self,
        api_method: str,
        *,
        method: str = "POST",
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base}/{api_method}"
        if client is not None:
            resp = await client.request(method, url, params=params, json=json, timeout=timeout)
        else:
            async with _ClientHolder(self._client) as c:
                resp = await c.request(method, url, params=params, json=json, timeout=timeout)
        resp.raise_for_status()
        body = resp.json()
        if isinstance(body, dict) and not body.get("ok", False):
            raise httpx.HTTPError(f"Telegram API error: {body.get('description', body)}")
        return body


def _normalize_update(update: dict[str, Any]) -> dict[str, Any]:
    """Flatten a Telegram update into a convenient, modality-agnostic payload.

    Surfaces ``chat_id`` / ``text`` / ``from`` for the common case while keeping
    the original sub-object under ``message`` and the update kind under ``kind``.
    """
    kind = next((k for k in _UPDATE_KINDS if isinstance(update.get(k), dict)), None)
    obj = update.get(kind, {}) if kind else {}
    # callback_query nests the chat under message; messages carry it directly.
    msg = obj.get("message", obj) if kind == "callback_query" else obj
    chat = msg.get("chat", {}) if isinstance(msg, dict) else {}
    payload: dict[str, Any] = {
        "update_id": update.get("update_id"),
        "kind": kind,
        "chat_id": chat.get("id"),
        "text": obj.get("text") or obj.get("data"),
        "from": obj.get("from"),
        "message": obj,
    }
    return payload


class _ClientHolder:
    """Use a caller-supplied AsyncClient, or own a temporary one for the block."""

    def __init__(self, client: httpx.AsyncClient | None) -> None:
        self._given = client
        self._owned: httpx.AsyncClient | None = None

    async def __aenter__(self) -> httpx.AsyncClient:
        if self._given is not None:
            return self._given
        self._owned = httpx.AsyncClient()
        return self._owned

    async def __aexit__(self, *exc: Any) -> None:
        if self._owned is not None:
            await self._owned.aclose()
