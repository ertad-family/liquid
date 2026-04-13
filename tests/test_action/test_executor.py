import pytest

from liquid.action.executor import ActionExecutor
from liquid.models.action import ActionConfig, ActionErrorType, ActionMapping
from liquid.models.schema import APISchema, AuthRequirement, Endpoint, EndpointKind
from liquid.sync.retry import RetryPolicy


class FakeVault:
    async def store(self, key: str, value: str) -> None:
        pass

    async def get(self, key: str) -> str:
        return "test-token"

    async def delete(self, key: str) -> None:
        pass


def _make_schema(endpoints: list[Endpoint] | None = None) -> APISchema:
    return APISchema(
        source_url="https://api.example.com",
        service_name="Example",
        discovery_method="openapi",
        endpoints=endpoints
        or [
            Endpoint(
                path="/orders",
                method="POST",
                kind=EndpointKind.WRITE,
                request_schema={
                    "required": ["amount"],
                    "properties": {
                        "amount": {"type": "number"},
                        "note": {"type": "string"},
                    },
                },
            ),
        ],
        auth=AuthRequirement(type="bearer", tier="A"),
    )


def _make_action(**kwargs) -> ActionConfig:
    defaults = {
        "endpoint_path": "/orders",
        "endpoint_method": "POST",
        "mappings": [ActionMapping(source_field="amount", target_path="amount")],
        "verified_by": "admin",
    }
    defaults.update(kwargs)
    return ActionConfig(**defaults)


def _make_transport(status: int, body: dict | None = None):
    """Create httpx mock transport returning a fixed response."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body or {})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
class TestActionExecutor:
    async def test_successful_post(self):
        import httpx

        transport = _make_transport(201, {"id": "ord_1", "amount": 100})
        async with httpx.AsyncClient(transport=transport) as client:
            executor = ActionExecutor(client, FakeVault())
            result = await executor.execute(
                action=_make_action(),
                data={"amount": 100},
                schema=_make_schema(),
                auth_ref="vault/example",
            )
        assert result.success
        assert result.status_code == 201
        assert result.response_body == {"id": "ord_1", "amount": 100}

    async def test_validation_error(self):
        import httpx

        transport = _make_transport(201, {})
        async with httpx.AsyncClient(transport=transport) as client:
            executor = ActionExecutor(client, FakeVault())
            result = await executor.execute(
                action=_make_action(),
                data={},  # missing required "amount"
                schema=_make_schema(),
                auth_ref="vault/example",
            )
        assert not result.success
        assert result.error.type == ActionErrorType.VALIDATION_ERROR

    async def test_404_returns_not_found(self):
        import httpx

        transport = _make_transport(404, {"error": "not found"})
        async with httpx.AsyncClient(transport=transport) as client:
            executor = ActionExecutor(client, FakeVault())
            result = await executor.execute(
                action=_make_action(),
                data={"amount": 100},
                schema=_make_schema(),
                auth_ref="vault/example",
            )
        assert not result.success
        assert result.error.type == ActionErrorType.NOT_FOUND

    async def test_409_returns_conflict(self):
        import httpx

        transport = _make_transport(409, {"error": "conflict"})
        async with httpx.AsyncClient(transport=transport) as client:
            executor = ActionExecutor(client, FakeVault())
            result = await executor.execute(
                action=_make_action(),
                data={"amount": 100},
                schema=_make_schema(),
                auth_ref="vault/example",
            )
        assert not result.success
        assert result.error.type == ActionErrorType.CONFLICT

    async def test_422_returns_unprocessable(self):
        import httpx

        transport = _make_transport(422, {"error": "bad data"})
        async with httpx.AsyncClient(transport=transport) as client:
            executor = ActionExecutor(client, FakeVault())
            result = await executor.execute(
                action=_make_action(),
                data={"amount": 100},
                schema=_make_schema(),
                auth_ref="vault/example",
            )
        assert not result.success
        assert result.error.type == ActionErrorType.UNPROCESSABLE

    async def test_endpoint_not_found(self):
        import httpx

        transport = _make_transport(200, {})
        async with httpx.AsyncClient(transport=transport) as client:
            executor = ActionExecutor(client, FakeVault())
            result = await executor.execute(
                action=_make_action(endpoint_path="/nonexistent"),
                data={"amount": 100},
                schema=_make_schema(),
                auth_ref="vault/example",
            )
        assert not result.success
        assert result.error.type == ActionErrorType.VALIDATION_ERROR

    async def test_idempotency_header(self):
        import httpx

        captured_headers = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_headers.update(dict(request.headers))
            return httpx.Response(200, json={})

        transport = httpx.MockTransport(handler)
        schema = _make_schema(
            [
                Endpoint(
                    path="/orders",
                    method="POST",
                    kind=EndpointKind.WRITE,
                    idempotency_header="Idempotency-Key",
                ),
            ]
        )
        async with httpx.AsyncClient(transport=transport) as client:
            executor = ActionExecutor(client, FakeVault())
            result = await executor.execute(
                action=_make_action(),
                data={"amount": 100},
                schema=schema,
                auth_ref="vault/example",
                idempotency_key="my-key-123",
            )
        assert result.success
        assert captured_headers.get("idempotency-key") == "my-key-123"
        assert result.idempotency_key == "my-key-123"

    async def test_retry_on_429(self):
        import httpx

        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return httpx.Response(429, json={"error": "rate limited"})
            return httpx.Response(200, json={"ok": True})

        transport = httpx.MockTransport(handler)
        policy = RetryPolicy(max_retries=3, base_delay=0.01, max_delay=0.1)
        async with httpx.AsyncClient(transport=transport) as client:
            executor = ActionExecutor(client, FakeVault(), retry_policy=policy)
            result = await executor.execute(
                action=_make_action(),
                data={"amount": 100},
                schema=_make_schema(),
                auth_ref="vault/example",
            )
        assert result.success
        assert call_count == 3

    async def test_server_error_exhausts_retries(self):
        import httpx

        transport = _make_transport(500, {"error": "internal"})
        policy = RetryPolicy(max_retries=1, base_delay=0.01, max_delay=0.1)
        async with httpx.AsyncClient(transport=transport) as client:
            executor = ActionExecutor(client, FakeVault(), retry_policy=policy)
            result = await executor.execute(
                action=_make_action(),
                data={"amount": 100},
                schema=_make_schema(),
                auth_ref="vault/example",
            )
        assert not result.success
        assert result.error.type == ActionErrorType.SERVER_ERROR


def _make_graphql_schema(endpoints: list[Endpoint] | None = None) -> APISchema:
    return APISchema(
        source_url="https://api.example.com",
        service_name="Example",
        discovery_method="graphql",
        endpoints=endpoints
        or [
            Endpoint(
                path="/graphql#mutation.createOrder",
                method="POST",
                kind=EndpointKind.WRITE,
                response_schema={"type": "object", "title": "Order"},
            ),
        ],
        auth=AuthRequirement(type="bearer", tier="A"),
    )


def _make_graphql_action(**kwargs) -> ActionConfig:
    defaults = {
        "endpoint_path": "/graphql#mutation.createOrder",
        "endpoint_method": "POST",
        "mappings": [
            ActionMapping(source_field="title", target_path="input.title"),
            ActionMapping(source_field="price", target_path="input.price"),
        ],
        "verified_by": "admin",
    }
    defaults.update(kwargs)
    return ActionConfig(**defaults)


@pytest.mark.asyncio
class TestGraphQLExecution:
    async def test_graphql_mutation_success(self):
        import httpx

        captured_body = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            captured_body.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "data": {"createOrder": {"id": "ord_1", "title": "Widget"}},
                },
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            executor = ActionExecutor(client, FakeVault())
            result = await executor.execute(
                action=_make_graphql_action(),
                data={"title": "Widget", "price": 9.99},
                schema=_make_graphql_schema(),
                auth_ref="vault/example",
            )

        assert result.success
        assert result.status_code == 200
        assert result.response_body == {"createOrder": {"id": "ord_1", "title": "Widget"}}

        # Verify the request was sent as a GraphQL mutation
        assert "query" in captured_body
        assert "mutation" in captured_body["query"]
        assert "createOrder" in captured_body["query"]
        assert "variables" in captured_body

    async def test_graphql_mutation_with_errors(self):
        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "errors": [{"message": "Invalid input: title is required"}],
                },
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            executor = ActionExecutor(client, FakeVault())
            result = await executor.execute(
                action=_make_graphql_action(),
                data={"title": "Widget", "price": 9.99},
                schema=_make_graphql_schema(),
                auth_ref="vault/example",
            )

        assert not result.success
        assert result.error.type == ActionErrorType.VALIDATION_ERROR
        assert "Invalid input" in result.error.message

    async def test_graphql_posts_to_graphql_endpoint(self):
        import httpx

        captured_url = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_url
            captured_url = str(request.url)
            return httpx.Response(200, json={"data": {"createOrder": {"id": "1"}}})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            executor = ActionExecutor(client, FakeVault())
            await executor.execute(
                action=_make_graphql_action(),
                data={"title": "Widget", "price": 9.99},
                schema=_make_graphql_schema(),
                auth_ref="vault/example",
            )

        assert captured_url == "https://api.example.com/graphql"

    async def test_graphql_server_error(self):
        import httpx

        transport = _make_transport(500, {"error": "internal"})
        policy = RetryPolicy(max_retries=0, base_delay=0.01, max_delay=0.01)
        async with httpx.AsyncClient(transport=transport) as client:
            executor = ActionExecutor(client, FakeVault(), retry_policy=policy)
            result = await executor.execute(
                action=_make_graphql_action(),
                data={"title": "Widget", "price": 9.99},
                schema=_make_graphql_schema(),
                auth_ref="vault/example",
            )
        assert not result.success
        assert result.error.type == ActionErrorType.SERVER_ERROR


def _make_mcp_schema(endpoints: list[Endpoint] | None = None) -> APISchema:
    return APISchema(
        source_url="https://api.example.com",
        service_name="Example",
        discovery_method="mcp",
        endpoints=endpoints
        or [
            Endpoint(
                path="/mcp/tools/create_order",
                method="POST",
                kind=EndpointKind.WRITE,
            ),
        ],
        auth=AuthRequirement(type="bearer", tier="A"),
    )


def _make_mcp_action(**kwargs) -> ActionConfig:
    defaults = {
        "endpoint_path": "/mcp/tools/create_order",
        "endpoint_method": "POST",
        "mappings": [
            ActionMapping(source_field="title", target_path="title"),
            ActionMapping(source_field="price", target_path="price"),
        ],
        "verified_by": "admin",
    }
    defaults.update(kwargs)
    return ActionConfig(**defaults)


@pytest.mark.asyncio
class TestMCPExecution:
    async def test_mcp_http_fallback_success(self):
        """When MCP SDK is not available, falls back to HTTP POST."""
        import httpx

        captured_body = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            captured_body.update(json.loads(request.content))
            return httpx.Response(200, json={"id": "ord_1", "title": "Widget"})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            executor = ActionExecutor(client, FakeVault())
            result = await executor.execute(
                action=_make_mcp_action(),
                data={"title": "Widget", "price": 9.99},
                schema=_make_mcp_schema(),
                auth_ref="vault/example",
            )

        assert result.success
        assert result.status_code == 200
        assert result.response_body == {"id": "ord_1", "title": "Widget"}
        assert captured_body.get("title") == "Widget"
        assert captured_body.get("price") == 9.99

    async def test_mcp_http_fallback_url(self):
        """HTTP fallback should POST to /mcp/tools/<tool_name>."""
        import httpx

        captured_url = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_url
            captured_url = str(request.url)
            return httpx.Response(200, json={"ok": True})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            executor = ActionExecutor(client, FakeVault())
            await executor.execute(
                action=_make_mcp_action(),
                data={"title": "Widget", "price": 9.99},
                schema=_make_mcp_schema(),
                auth_ref="vault/example",
            )

        assert captured_url == "https://api.example.com/mcp/tools/create_order"

    async def test_mcp_http_fallback_error(self):
        import httpx

        transport = _make_transport(400, {"error": "bad request"})
        async with httpx.AsyncClient(transport=transport) as client:
            executor = ActionExecutor(client, FakeVault())
            result = await executor.execute(
                action=_make_mcp_action(),
                data={"title": "Widget", "price": 9.99},
                schema=_make_mcp_schema(),
                auth_ref="vault/example",
            )
        assert not result.success
        assert result.error.type == ActionErrorType.VALIDATION_ERROR

    async def test_mcp_endpoint_not_found(self):
        import httpx

        transport = _make_transport(200, {})
        async with httpx.AsyncClient(transport=transport) as client:
            executor = ActionExecutor(client, FakeVault())
            result = await executor.execute(
                action=_make_mcp_action(endpoint_path="/mcp/tools/nonexistent"),
                data={"title": "Widget"},
                schema=_make_mcp_schema(),
                auth_ref="vault/example",
            )
        assert not result.success
        assert result.error.type == ActionErrorType.VALIDATION_ERROR
