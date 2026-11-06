"""Preset 1: two Spanish knowledge documents and bounded event lifetimes.

Provider operations live in elevenlabs.py; approval and identity remain host policy.
No school/agent id or credential is baked into this reusable preset.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from liquid.approval.models import digest


class KnowledgeItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kind: Literal["fact", "event"]
    topic: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source: str = "director"
    source_ref: str | None = None
    status: Literal["active", "scheduled", "expired", "archived"] = "active"
    starts_on: str | None = None
    ends_on: str | None = None
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_window(self) -> KnowledgeItem:
        if self.kind == "event" and self.ends_on is None:
            raise ValueError("Events require an explicit end date/time")
        if self.kind == "fact" and (self.starts_on or self.ends_on):
            raise ValueError("Facts do not carry event windows")
        return self


def boundary(value: str, timezone: str, *, end: bool = False) -> datetime:
    """Dates include the whole local end day; datetime ends are exclusive.

    Naive datetimes must name an offset: DST gaps/folds otherwise silently change
    meaning. Date-only local midnight is calculated with the client's IANA zone.
    """
    zone = ZoneInfo(timezone)
    if len(value) == 10:
        day = date.fromisoformat(value)
        if end:
            day += timedelta(days=1)
        return datetime.combine(day, time.min, zone)
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError("Datetime boundaries need an explicit UTC offset")
    return stamp.astimezone(zone)


def validate_items(items: list[KnowledgeItem], timezone: str) -> None:
    ZoneInfo(timezone)
    if len({item.id for item in items}) != len(items):
        raise ValueError("Duplicate knowledge item id")
    for item in items:
        start = boundary(item.starts_on, timezone) if item.starts_on else None
        end = boundary(item.ends_on, timezone, end=True) if item.ends_on else None
        if start and end and start >= end:
            raise ValueError("Event start must be before its end")


def item_status(item: KnowledgeItem, now: datetime, timezone: str) -> str:
    if now.tzinfo is None:
        raise ValueError("Rendering clock must be timezone-aware")
    if item.status in ("archived", "expired"):
        return item.status
    if item.kind == "fact":
        return "active"
    if item.ends_on and now >= boundary(item.ends_on, timezone, end=True):
        return "expired"
    if item.starts_on and now < boundary(item.starts_on, timezone):
        return "scheduled"
    return "active"


def render_documents(items: list[KnowledgeItem], now: datetime, timezone: str = "America/Santiago") -> dict[str, str]:
    validate_items(items, timezone)
    facts = ["# Datos", "Información permanente confirmada por el cliente.", ""]
    events = [
        "# Avisos y eventos vigentes",
        "Comunicar solamente dentro del plazo indicado. No comunicar avisos vencidos.",
        "",
    ]
    previous_topic = None
    for item in sorted(items, key=lambda i: (i.kind, i.topic, i.id)):
        if item_status(item, now, timezone) != "active":
            continue
        if item.kind == "fact":
            if previous_topic != item.topic:
                facts.extend([f"## {item.topic}", ""])
                previous_topic = item.topic
            facts.append(f"- {item.text}")
        else:
            events.append(f"- [{item.topic}] {item.text} (vigente hasta {item.ends_on})")
    if len(events) == 3:
        events.append("No hay avisos vigentes.")
    return {"facts": "\n".join(facts) + "\n", "events": "\n".join(events) + "\n"}


class KnowledgeStateStore(Protocol):
    async def load(self, client_id: str) -> dict[str, Any]: ...
    async def save(self, client_id: str, state: dict[str, Any]) -> None: ...


class InMemoryKnowledgeStateStore:
    def __init__(self) -> None:
        self._states: dict[str, dict[str, Any]] = {}

    async def load(self, client_id: str) -> dict[str, Any]:
        return deepcopy(self._states[client_id])

    async def save(self, client_id: str, state: dict[str, Any]) -> None:
        self._states[client_id] = deepcopy(state)


class ExpiryRule:
    """Propose both activation and expiry when the rendered window changes."""

    name = "voice-knowledge-expiry"

    def __init__(self, timezone: str = "America/Santiago") -> None:
        self.timezone = timezone

    def evaluate(self, state: dict[str, Any], now: datetime) -> dict[str, Any] | None:
        items = [KnowledgeItem.model_validate(i) for i in state["items"]]
        rendered = render_documents(items, now, self.timezone)
        if rendered == state["documents"]:
            return None
        return {"operations": [], "as_of": now.isoformat()}


def project_knowledge(before: dict[str, Any], request: dict[str, Any], timezone: str) -> dict[str, Any]:
    if set(request) - {"operations", "as_of"}:
        raise ValueError("Unknown knowledge request fields")
    if before["published_hashes"] != {key: digest(text) for key, text in before["documents"].items()}:
        raise ValueError("Managed documents drifted; reconcile with an operator before proposing")
    after = deepcopy(before)
    items = {i["id"]: KnowledgeItem.model_validate(i) for i in before["items"]}
    for operation in request.get("operations", []):
        op = operation["op"]
        if op == "add":
            item = KnowledgeItem.model_validate(operation["item"])
            if item.id in items:
                raise ValueError("Item already exists")
            items[item.id] = item
        elif op == "update":
            old = items[operation["id"]]
            changes = operation["changes"]
            if set(changes) - {"topic", "text", "starts_on", "ends_on"}:
                raise ValueError("Only content and event windows may be updated")
            items[old.id] = KnowledgeItem.model_validate(
                {
                    **old.model_dump(),
                    **changes,
                    "version": old.version + 1,
                    "status": "active" if old.status != "archived" else "archived",
                }
            )
        elif op == "archive":
            old = items[operation["id"]]
            items[old.id] = old.model_copy(update={"status": "archived", "version": old.version + 1})
        else:
            raise ValueError(f"Unsupported knowledge operation {op!r}")
    now = datetime.fromisoformat(request.get("as_of", before["as_of"]))
    validate_items(list(items.values()), timezone)
    after["items"] = [
        i.model_copy(update={"status": item_status(i, now, timezone)}).model_dump()
        for i in sorted(items.values(), key=lambda i: i.id)
    ]
    after["as_of"] = now.isoformat()
    after["documents"] = render_documents(list(items.values()), now, timezone)
    after["published_hashes"] = {key: digest(text) for key, text in after["documents"].items()}
    return after
