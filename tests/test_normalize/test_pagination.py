from __future__ import annotations

from liquid.normalize import normalize_pagination


class TestStripeStyle:
    def test_stripe_envelope(self):
        resp = {
            "object": "list",
            "data": [{"id": "a"}, {"id": "b"}],
            "has_more": True,
            "url": "/v1/customers",
        }
        env = normalize_pagination(resp)
        assert env.items == [{"id": "a"}, {"id": "b"}]
        assert env.has_more is True
        assert env.original == resp

    def test_stripe_no_more(self):
        env = normalize_pagination({"object": "list", "data": [{"id": "z"}], "has_more": False})
        assert env.has_more is False
        assert env.next_cursor is None


class TestDRFStyle:
    def test_drf_cursor(self):
        resp = {
            "count": 42,
            "next": "https://api.example.com/items?page=2",
            "previous": None,
            "results": [{"id": 1}, {"id": 2}],
        }
        env = normalize_pagination(resp)
        assert env.items == [{"id": 1}, {"id": 2}]
        assert env.next_cursor == "https://api.example.com/items?page=2"
        assert env.prev_cursor is None
        assert env.total_count == 42
        assert env.has_more is True  # derived from presence of next cursor

    def test_drf_last_page(self):
        env = normalize_pagination(
            {
                "count": 10,
                "next": None,
                "previous": "https://api.example.com/items?page=2",
                "results": [{"id": 9}],
            }
        )
        assert env.next_cursor is None
        assert env.prev_cursor == "https://api.example.com/items?page=2"
        assert env.has_more is None


class TestPageNumberStyle:
    def test_page_numbers(self):
        resp = {
            "items": [{"id": "a"}, {"id": "b"}],
            "page": 1,
            "per_page": 2,
            "total_pages": 5,
            "total": 42,
        }
        env = normalize_pagination(resp)
        assert env.items == [{"id": "a"}, {"id": "b"}]
        assert env.page == 1
        assert env.per_page == 2
        assert env.total_count == 42


class TestRawList:
    def test_raw_list(self):
        env = normalize_pagination([{"id": 1}, {"id": 2}])
        assert env.items == [{"id": 1}, {"id": 2}]
        assert env.has_more is None
        assert env.next_cursor is None
        assert env.total_count is None


class TestEmpty:
    def test_empty_dict(self):
        env = normalize_pagination({})
        assert env.items == []
        assert env.has_more is None

    def test_empty_list(self):
        env = normalize_pagination([])
        assert env.items == []


class TestExplicitItemsKey:
    def test_override_items_key(self):
        resp = {"posts": [{"id": 1}], "meta": {"total": 1}}
        env = normalize_pagination(resp, items_key="posts")
        assert env.items == [{"id": 1}]


class TestGenericCursor:
    def test_next_cursor_field(self):
        resp = {"data": [{"id": "a"}], "next_cursor": "abc", "prev_cursor": None}
        env = normalize_pagination(resp)
        assert env.next_cursor == "abc"
        assert env.has_more is True
