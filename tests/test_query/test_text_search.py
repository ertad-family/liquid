"""Tests for the pure (in-memory) text-search scorer."""

from __future__ import annotations

from liquid.query.text_search import search_async, search_records


def _emails() -> list[dict]:
    return [
        {
            "id": 1,
            "subject": "Q3 planning sync",
            "body": "Discuss the Q3 roadmap and blockers next Monday.",
            "sender": "alice@example.com",
        },
        {
            "id": 2,
            "subject": "Lunch?",
            "body": "Want to grab lunch today?",
            "sender": "bob@example.com",
        },
        {
            "id": 3,
            "subject": "Quarterly numbers",
            "body": "Q3 revenue is up 12%. See attached dashboard.",
            "sender": "carol@example.com",
        },
    ]


class TestSearchRecords:
    def test_basic_match(self) -> None:
        results = search_records(_emails(), "Q3 planning")
        # Both id=1 and id=3 mention Q3 — id=1 also mentions planning
        ids = [r["record"]["id"] for r in results]
        assert ids[0] == 1  # strongest match
        assert 3 in ids

    def test_case_insensitive(self) -> None:
        results = search_records(_emails(), "LUNCH")
        assert len(results) >= 1
        assert results[0]["record"]["id"] == 2

    def test_no_match(self) -> None:
        results = search_records(_emails(), "xylophone")
        assert results == []

    def test_empty_query(self) -> None:
        assert search_records(_emails(), "") == []
        assert search_records(_emails(), "   ") == []

    def test_scores_normalized(self) -> None:
        results = search_records(_emails(), "lunch")
        assert results[0]["score"] == 1.0
        for r in results:
            assert 0.0 < r["score"] <= 1.0

    def test_matched_fields_reported(self) -> None:
        results = search_records(_emails(), "Q3")
        for r in results:
            assert r["matched_fields"]  # non-empty
            assert all(isinstance(f, str) for f in r["matched_fields"])

    def test_fields_filter(self) -> None:
        # Only search the subject — body mentions of Q3 should be ignored.
        results = search_records(_emails(), "roadmap", fields=["subject"])
        # "roadmap" is in body but not subject of any email
        assert results == []

    def test_fields_filter_hits(self) -> None:
        results = search_records(_emails(), "Quarterly", fields=["subject"])
        assert len(results) == 1
        assert results[0]["record"]["id"] == 3
        assert results[0]["matched_fields"] == ["subject"]

    def test_short_subject_outranks_long_body(self) -> None:
        records = [
            {"id": "long", "body": "foo " * 200 + "banana " + "bar " * 200},
            {"id": "short", "subject": "banana"},
        ]
        results = search_records(records, "banana")
        # The short subject-only field should score higher due to length dampening
        assert results[0]["record"]["id"] == "short"

    def test_limit(self) -> None:
        results = search_records(_emails(), "example", limit=1)
        assert len(results) == 1


class TestSearchAsync:
    async def test_walks_pages(self) -> None:
        pages = [
            [{"id": 1, "subject": "Q3 planning"}],
            [{"id": 2, "subject": "Q4 planning"}],
        ]

        async def page_iter():
            for page in pages:
                yield page

        results = await search_async(page_iter(), "planning")
        ids = sorted(r["record"]["id"] for r in results)
        assert ids == [1, 2]

    async def test_scan_limit_stops_early(self) -> None:
        async def page_iter():
            yield [{"id": i, "subject": "planning"} for i in range(10)]
            yield [{"id": i, "subject": "planning"} for i in range(10, 20)]

        results = await search_async(page_iter(), "planning", scan_limit=5)
        # only the first 5 should be scored
        assert len(results) == 5
