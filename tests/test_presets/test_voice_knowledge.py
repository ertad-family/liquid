from __future__ import annotations

from datetime import UTC, datetime

import pytest

from liquid.presets.voice_knowledge import (
    ExpiryRule,
    KnowledgeItem,
    boundary,
    item_status,
    project_knowledge,
    render_documents,
)


def stamp(value):
    return datetime.fromisoformat(value)


def event(**changes):
    return KnowledgeItem.model_validate(
        {
            "id": "graduation",
            "kind": "event",
            "topic": "Colegio",
            "text": "Fiesta de graduación a las 18:00",
            "starts_on": "2026-12-01",
            "ends_on": "2026-12-05",
            **changes,
        }
    )


def test_render_is_deterministic_spanish_sorted_and_keeps_empty_header():
    facts = [
        KnowledgeItem(id=i, kind="fact", topic="Horario", text=text)
        for i, text in [("b", "Viernes hasta las 13:00"), ("a", "Lunes desde las 08:00")]
    ]
    now = stamp("2026-12-03T12:00:00-03:00")
    docs = render_documents([event(), *facts], now)
    assert docs == render_documents([*reversed(facts), event()], now)
    assert docs["facts"].index("Lunes") < docs["facts"].index("Viernes")
    assert "vigente hasta 2026-12-05" in docs["events"]
    assert "No comunicar avisos vencidos" in docs["events"]
    empty = render_documents([], now)
    assert empty["facts"].strip() and "No hay avisos vigentes" in empty["events"]


@pytest.mark.parametrize(
    "now,expected",
    [
        ("2026-11-30T23:59:59-03:00", "scheduled"),
        ("2026-12-01T00:00:00-03:00", "active"),
        ("2026-12-05T23:59:59-03:00", "active"),
        ("2026-12-06T00:00:00-03:00", "expired"),
    ],
)
def test_local_date_window_is_start_inclusive_and_end_day_inclusive(now, expected):
    assert item_status(event(), stamp(now), "America/Santiago") == expected


@pytest.mark.parametrize(
    "now,expected",
    [
        ("2026-12-05T17:59:59-03:00", "scheduled"),
        ("2026-12-05T18:00:00-03:00", "active"),
        ("2026-12-05T19:00:00-03:00", "expired"),
    ],
)
def test_datetime_window_has_exclusive_end(now, expected):
    item = event(starts_on="2026-12-05T18:00:00-03:00", ends_on="2026-12-05T19:00:00-03:00")
    assert item_status(item, stamp(now), "America/Santiago") == expected


def test_chile_dst_and_naive_datetimes():
    assert boundary("2026-07-01", "America/Santiago").utcoffset().total_seconds() == -4 * 3600
    assert boundary("2026-12-01", "America/Santiago").utcoffset().total_seconds() == -3 * 3600
    with pytest.raises(ValueError, match="offset"):
        boundary("2026-12-01T12:00:00", "America/Santiago")
    with pytest.raises(ValueError, match="aware"):
        render_documents([event()], datetime(2026, 12, 1))


def test_invalid_events_and_duplicate_ids_rejected():
    with pytest.raises(ValueError, match="end date"):
        event(ends_on=None)
    with pytest.raises(ValueError, match="before its end"):
        render_documents([event(ends_on="2026-11-01")], datetime.now(UTC))
    with pytest.raises(ValueError, match="Duplicate"):
        render_documents([event(), event()], datetime.now(UTC))


def test_expiry_rule_emits_request_only_when_rendering_changes():
    active = stamp("2026-12-05T23:59:59-03:00")
    expired = stamp("2026-12-06T00:00:00-03:00")
    state = {"items": [event().model_dump()], "documents": render_documents([event()], active)}
    rule = ExpiryRule()
    assert rule.evaluate(state, active) is None
    assert rule.evaluate(state, expired) == {"operations": [], "as_of": expired.isoformat()}


def test_project_add_update_archive_versions_and_drift():
    from liquid.approval.models import digest

    now = stamp("2026-12-03T12:00:00-03:00")
    docs = render_documents([], now)
    state = {
        "items": [],
        "documents": docs,
        "as_of": now.isoformat(),
        "published_hashes": {k: digest(v) for k, v in docs.items()},
    }
    state = project_knowledge(state, {"operations": [{"op": "add", "item": event().model_dump()}]}, "America/Santiago")
    assert state["items"][0]["version"] == 1
    state = project_knowledge(
        state,
        {
            "operations": [
                {
                    "op": "update",
                    "id": "graduation",
                    "changes": {
                        "text": "Fiesta de graduación a las 19:00",
                    },
                }
            ]
        },
        "America/Santiago",
    )
    assert state["items"][0]["version"] == 2
    assert "19:00" in state["documents"]["events"] and "18:00" not in state["documents"]["events"]
    state = project_knowledge(state, {"operations": [{"op": "archive", "id": "graduation"}]}, "America/Santiago")
    assert state["items"][0]["status"] == "archived"
    assert "Fiesta" not in state["documents"]["events"]
    state["documents"]["facts"] += "Manual edit"
    with pytest.raises(ValueError, match="drift"):
        project_knowledge(state, {"operations": []}, "America/Santiago")
