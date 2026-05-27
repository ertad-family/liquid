"""GraphQL transport driver.

GraphQL rides HTTP, so the shared httpx client / SSRF guard / auth all apply —
but the request is a POST of ``{"query": ..., "variables": ...}`` and the
response nests the records under ``data.<field>``, neither of which the HTTP
driver handles. Discovery pre-computes the selection set and argument types into
``Endpoint.transport_meta`` (see :mod:`liquid.discovery.graphql`); this driver
renders the operation string deterministically and unwraps the result.

Relay connections (``edges { node } … pageInfo``) are flattened to the node
objects, and ``pageInfo.endCursor`` drives cursor pagination via the ``after``
variable — so a GraphQL adapter paginates through the same Fetcher loop as REST.

GraphQL reports query errors as HTTP 200 with an ``errors`` array. When there's
no usable ``data`` we surface a synthetic error status with ``raw=None`` so the
Fetcher's normalized error mapping fires instead of seeing a "successful" 200.
"""

from __future__ import annotations

import json

from liquid.transport.base import DriverResponse, FetchContext


class GraphQLDriver:
    scheme = "graphql"

    async def fetch(self, ctx: FetchContext) -> DriverResponse:
        assert ctx.http_client is not None, "GraphQL driver requires an http_client"
        meta = ctx.endpoint.transport_meta or {}
        gql_path = meta.get("gql_path") or ctx.endpoint.path.split("#", 1)[0]
        query, variables = _render_operation(meta, ctx.params, ctx.cursor)

        payload: dict[str, object] = {"query": query}
        if variables:
            payload["variables"] = variables

        url = f"{ctx.base_url.rstrip('/')}{gql_path}"
        response = await ctx.http_client.post(
            url,
            json=payload,
            headers=ctx.headers,
            auth=ctx.auth,
            follow_redirects=True,
        )
        headers = dict(response.headers)

        # HTTP-level failure (e.g. 401/429) — let the Fetcher map it via raw.
        if not response.is_success:
            return DriverResponse(
                status_code=response.status_code,
                headers=headers,
                error_body=response.text[:500],
                raw=response,
            )

        body = response.json()
        data = body.get("data") if isinstance(body, dict) else None
        errors = body.get("errors") if isinstance(body, dict) else None
        if errors and not data:
            # GraphQL-level error on a 200 — synthesize a status, raw=None so the
            # Fetcher raises rather than treating the 200 as success.
            return DriverResponse(
                status_code=422,
                headers=headers,
                error_body=json.dumps(errors)[:500],
            )

        records, next_cursor = _extract_records(meta, data)
        return DriverResponse(
            status_code=response.status_code,
            headers=headers,
            records=records,
            next_cursor=next_cursor,
        )


def _render_operation(
    meta: dict,
    params: dict | None,
    cursor: str | None,
) -> tuple[str, dict]:
    """Render a GraphQL operation string + variables from discovery metadata."""
    operation = meta.get("operation", "query")
    field = meta.get("field", "")
    selection = meta.get("selection") or ""
    connection = bool(meta.get("connection"))
    args: dict = meta.get("args", {}) or {}

    # Only pass through caller params that the field actually declares as args.
    variables = {k: v for k, v in (params or {}).items() if k in args}
    if connection and cursor and "after" in args:
        variables["after"] = cursor

    used = list(variables.keys())
    if used:
        decl = "(" + ", ".join(f"${k}: {args[k].get('type', 'String')}" for k in used) + ")"
        field_args = "(" + ", ".join(f"{k}: ${k}" for k in used) + ")"
    else:
        decl = ""
        field_args = ""

    if connection:
        sel = f" {{ edges {{ node {{ {selection} }} }} pageInfo {{ endCursor hasNextPage }} }}"
    elif selection:
        sel = f" {{ {selection} }}"
    else:
        sel = ""

    query = f"{operation}{decl} {{ {field}{field_args}{sel} }}"
    return " ".join(query.split()), variables


def _extract_records(meta: dict, data: object) -> tuple[list[dict], str | None]:
    field = meta.get("field", "")
    connection = bool(meta.get("connection"))
    node = data.get(field) if isinstance(data, dict) else None
    if node is None:
        return [], None

    if connection and isinstance(node, dict):
        edges = node.get("edges") or []
        records = [e["node"] for e in edges if isinstance(e, dict) and isinstance(e.get("node"), dict)]
        page = node.get("pageInfo") or {}
        next_cursor = page.get("endCursor") if page.get("hasNextPage") else None
        return records, next_cursor

    if isinstance(node, list):
        return [r for r in node if isinstance(r, dict)], None
    if isinstance(node, dict):
        return [node], None
    # Scalar return — wrap so the mapper still has a record to work with.
    return [{field: node}], None
