import httpx

from liquid.sync.pagination import (
    CursorPagination,
    LinkHeaderPagination,
    NoPagination,
    OffsetPagination,
    PageNumberPagination,
    PaginationStrategy,
)


def _make_response(
    data: dict | list,
    headers: dict | None = None,
    url: str = "https://api.test.com/items",
) -> httpx.Response:
    request = httpx.Request("GET", url)
    return httpx.Response(200, json=data, headers=headers or {}, request=request)


class TestNoPagination:
    def test_conforms_to_protocol(self):
        assert isinstance(NoPagination(), PaginationStrategy)

    def test_returns_empty_params(self):
        p = NoPagination()
        assert p.get_request_params(None) == {}
        assert p.get_request_params("abc") == {}

    def test_returns_no_cursor(self):
        p = NoPagination()
        resp = _make_response({"data": []})
        assert p.extract_next_cursor(resp) is None


class TestCursorPagination:
    def test_no_cursor_first_page(self):
        p = CursorPagination()
        assert p.get_request_params(None) == {}

    def test_cursor_in_params(self):
        p = CursorPagination(cursor_param="after")
        assert p.get_request_params("abc123") == {"after": "abc123"}

    def test_extract_cursor(self):
        p = CursorPagination(response_cursor_path="pagination.next")
        resp = _make_response({"data": [], "pagination": {"next": "xyz"}})
        assert p.extract_next_cursor(resp) == "xyz"

    def test_no_more_pages(self):
        p = CursorPagination()
        resp = _make_response({"data": [], "next_cursor": None})
        assert p.extract_next_cursor(resp) is None


class TestOffsetPagination:
    def test_first_page(self):
        p = OffsetPagination(limit=50)
        params = p.get_request_params(None)
        assert params == {"offset": 0, "limit": 50}

    def test_subsequent_page(self):
        p = OffsetPagination(limit=50)
        params = p.get_request_params("100")
        assert params == {"offset": 100, "limit": 50}

    def test_has_more(self):
        p = OffsetPagination(limit=2)
        resp = _make_response({"data": [1, 2]}, url="https://api.test.com/items?offset=0&limit=2")
        assert p.extract_next_cursor(resp) == "2"

    def test_last_page(self):
        p = OffsetPagination(limit=10)
        resp = _make_response({"data": [1, 2, 3]}, url="https://api.test.com/items?offset=0&limit=10")
        assert p.extract_next_cursor(resp) is None


class TestPageNumberPagination:
    def test_first_page(self):
        p = PageNumberPagination(per_page=25)
        params = p.get_request_params(None)
        assert params == {"page": 1, "per_page": 25}

    def test_page_3(self):
        p = PageNumberPagination()
        params = p.get_request_params("3")
        assert params["page"] == 3

    def test_has_more(self):
        p = PageNumberPagination(per_page=2)
        resp = _make_response({"data": [1, 2]}, url="https://api.test.com/items?page=1&per_page=2")
        assert p.extract_next_cursor(resp) == "2"


class TestLinkHeaderPagination:
    def test_has_next(self):
        p = LinkHeaderPagination()
        resp = _make_response(
            {"data": []},
            headers={
                "link": '<https://api.test.com/items?page=2>; rel="next", '
                '<https://api.test.com/items?page=10>; rel="last"'
            },
        )
        assert p.extract_next_cursor(resp) == "https://api.test.com/items?page=2"

    def test_no_next(self):
        p = LinkHeaderPagination()
        resp = _make_response({"data": []}, headers={"link": '<https://api.test.com/items?page=1>; rel="first"'})
        assert p.extract_next_cursor(resp) is None

    def test_no_link_header(self):
        p = LinkHeaderPagination()
        resp = _make_response({"data": []})
        assert p.extract_next_cursor(resp) is None
