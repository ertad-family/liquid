from __future__ import annotations

import re
from typing import Any, Protocol, runtime_checkable

import httpx  # noqa: TC002


@runtime_checkable
class PaginationStrategy(Protocol):
    def get_request_params(self, cursor: str | None) -> dict[str, Any]:
        """Return query params to add for this page."""
        ...

    def extract_next_cursor(self, response: httpx.Response) -> str | None:
        """Extract next cursor from response. Returns None if no more pages."""
        ...


class NoPagination:
    def get_request_params(self, cursor: str | None) -> dict[str, Any]:
        return {}

    def extract_next_cursor(self, response: httpx.Response) -> str | None:
        return None


class CursorPagination:
    def __init__(self, cursor_param: str = "cursor", response_cursor_path: str = "next_cursor") -> None:
        self.cursor_param = cursor_param
        self.response_cursor_path = response_cursor_path

    def get_request_params(self, cursor: str | None) -> dict[str, Any]:
        if cursor is None:
            return {}
        return {self.cursor_param: cursor}

    def extract_next_cursor(self, response: httpx.Response) -> str | None:
        data = response.json()
        value = _extract_nested(data, self.response_cursor_path)
        return str(value) if value else None


class OffsetPagination:
    def __init__(self, offset_param: str = "offset", limit_param: str = "limit", limit: int = 100) -> None:
        self.offset_param = offset_param
        self.limit_param = limit_param
        self.limit = limit

    def get_request_params(self, cursor: str | None) -> dict[str, Any]:
        offset = int(cursor) if cursor else 0
        return {self.offset_param: offset, self.limit_param: self.limit}

    def extract_next_cursor(self, response: httpx.Response) -> str | None:
        data = response.json()
        records = data if isinstance(data, list) else data.get("data", data.get("results", []))
        if isinstance(records, list) and len(records) >= self.limit:
            current_offset = int(response.request.url.params.get(self.offset_param, "0"))
            return str(current_offset + self.limit)
        return None


class PageNumberPagination:
    def __init__(self, page_param: str = "page", per_page_param: str = "per_page", per_page: int = 100) -> None:
        self.page_param = page_param
        self.per_page_param = per_page_param
        self.per_page = per_page

    def get_request_params(self, cursor: str | None) -> dict[str, Any]:
        page = int(cursor) if cursor else 1
        return {self.page_param: page, self.per_page_param: self.per_page}

    def extract_next_cursor(self, response: httpx.Response) -> str | None:
        data = response.json()
        records = data if isinstance(data, list) else data.get("data", data.get("results", []))
        if isinstance(records, list) and len(records) >= self.per_page:
            current_page = int(response.request.url.params.get(self.page_param, "1"))
            return str(current_page + 1)
        return None


class LinkHeaderPagination:
    _LINK_RE = re.compile(r'<([^>]+)>;\s*rel="next"')

    def get_request_params(self, cursor: str | None) -> dict[str, Any]:
        return {}

    def extract_next_cursor(self, response: httpx.Response) -> str | None:
        link_header = response.headers.get("link", "")
        match = self._LINK_RE.search(link_header)
        return match.group(1) if match else None


def _extract_nested(data: dict[str, Any], path: str) -> Any:
    parts = path.split(".")
    current: Any = data
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current
