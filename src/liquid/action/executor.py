"""ActionExecutor — runtime engine for write operations."""

from __future__ import annotations

import json as _json
import logging
import re
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx  # noqa: TC002

from liquid.action.builder import RequestBodyBuilder
from liquid.action.path import PathResolver
from liquid.action.validator import RequestValidator
from liquid.exceptions import RateLimitError, ServiceDownError
from liquid.models.action import ActionConfig, ActionError, ActionErrorType, ActionResult
from liquid.sync.retry import WRITE_RETRY_DEFAULTS, RetryPolicy, with_retry

if TYPE_CHECKING:
    from liquid.models.schema import APISchema, Endpoint
    from liquid.protocols import Vault
    from liquid.sync.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_STATUS_TO_ERROR: dict[int, ActionErrorType] = {
    400: ActionErrorType.VALIDATION_ERROR,
    401: ActionErrorType.AUTH_ERROR,
    403: ActionErrorType.AUTH_ERROR,
    404: ActionErrorType.NOT_FOUND,
    409: ActionErrorType.CONFLICT,
    422: ActionErrorType.UNPROCESSABLE,
}

_GRAPHQL_MUTATION_RE = re.compile(r"^/graphql#mutation\.(.+)$")
_MCP_TOOL_RE = re.compile(r"^/mcp/tools/(.+)$")


def _action_error_for_exception(exc: RateLimitError | ServiceDownError) -> ActionError:
    """Build an ActionError from a transient exception raised during request send."""
    if isinstance(exc, RateLimitError):
        retry_after = exc.retry_after
        hint = f"Rate limited — retry after {retry_after}s" if retry_after else "Rate limited — backoff and retry"
        return ActionError(
            type=ActionErrorType.RATE_LIMIT,
            message=str(exc),
            recovery_hint=hint,
            details={"retry_after": retry_after} if retry_after else None,
        )
    return ActionError(
        type=ActionErrorType.SERVER_ERROR,
        message=str(exc),
        recovery_hint="Upstream server error — retry with backoff",
    )


def _action_error_for_status(status_code: int, message: str) -> ActionError:
    """Build an ActionError with recovery hint appropriate for an HTTP status."""
    error_type = _STATUS_TO_ERROR.get(status_code, ActionErrorType.SERVER_ERROR)
    details: dict[str, Any] = {"status": status_code, "body": message}

    if status_code in (401, 403):
        return ActionError(
            type=error_type,
            message=message,
            recovery_hint="Credentials invalid for this action",
            details=details,
        )
    if status_code == 404:
        return ActionError(
            type=error_type,
            message=message,
            recovery_hint="Resource not found — check ID or run repair_adapter()",
            auto_repair_available=True,
            details=details,
        )
    if status_code == 409:
        return ActionError(
            type=error_type,
            message=message,
            recovery_hint="Resource conflict — check if it already exists or use idempotency_key",
            details=details,
        )
    if status_code == 422:
        return ActionError(
            type=error_type,
            message=message,
            recovery_hint="Invalid request data — check request_schema requirements",
            details=details,
        )
    if status_code == 400:
        return ActionError(
            type=error_type,
            message=message,
            recovery_hint="Bad request — check request payload format",
            details=details,
        )
    if status_code >= 500:
        return ActionError(
            type=error_type,
            message=message,
            recovery_hint="Upstream server error — retry with backoff",
            details=details,
        )
    return ActionError(
        type=error_type,
        message=message,
        details=details,
    )


class ActionExecutor:
    """Executes write actions against external APIs."""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        vault: Vault,
        retry_policy: RetryPolicy | None = None,
        rate_limiter: RateLimiter | None = None,
        respect_rate_limit: bool = True,
        adapter_id: str | None = None,
    ) -> None:
        self.http_client = http_client
        self.vault = vault
        self.retry_policy = retry_policy or WRITE_RETRY_DEFAULTS
        self.rate_limiter = rate_limiter
        self.respect_rate_limit = respect_rate_limit
        self.adapter_id = adapter_id

    def _rate_key(self, endpoint_path: str) -> str:
        return f"{self.adapter_id or 'anon'}:{endpoint_path}"

    async def execute(
        self,
        action: ActionConfig,
        data: dict[str, Any],
        schema: APISchema,
        auth_ref: str,
        idempotency_key: str | None = None,
    ) -> ActionResult:
        """Execute a single write action.

        Steps:
        1. Find endpoint in schema
        2. Validate data against request_schema
        3. Detect protocol (REST / GraphQL / MCP) and dispatch
        """
        endpoint = next(
            (ep for ep in schema.endpoints if ep.path == action.endpoint_path and ep.method == action.endpoint_method),
            None,
        )
        if endpoint is None:
            return ActionResult(
                action_id=action.action_id,
                endpoint_path=action.endpoint_path,
                method=action.endpoint_method,
                status_code=0,
                success=False,
                error=ActionError(
                    type=ActionErrorType.VALIDATION_ERROR,
                    message=f"Endpoint {action.endpoint_method} {action.endpoint_path} not found in schema",
                    recovery_hint="Endpoint missing from schema — run repair_adapter() to re-discover",
                    auto_repair_available=True,
                ),
            )

        if endpoint.request_schema:
            validator = RequestValidator()
            errors = validator.validate(data, endpoint.request_schema)
            if errors:
                return ActionResult(
                    action_id=action.action_id,
                    endpoint_path=action.endpoint_path,
                    method=action.endpoint_method,
                    status_code=0,
                    success=False,
                    error=ActionError(
                        type=ActionErrorType.VALIDATION_ERROR,
                        message="Request validation failed",
                        details={"errors": errors},
                        recovery_hint="Fix request data to match request_schema requirements",
                    ),
                )

        # Dispatch based on endpoint path pattern
        if _GRAPHQL_MUTATION_RE.match(action.endpoint_path):
            return await self._execute_graphql(action, data, schema, auth_ref, endpoint, idempotency_key)

        if _MCP_TOOL_RE.match(action.endpoint_path):
            return await self._execute_mcp(action, data, schema, endpoint)

        return await self._execute_rest(action, data, schema, auth_ref, endpoint, idempotency_key)

    async def _execute_rest(
        self,
        action: ActionConfig,
        data: dict[str, Any],
        schema: APISchema,
        auth_ref: str,
        endpoint: Endpoint,
        idempotency_key: str | None = None,
    ) -> ActionResult:
        """Execute a standard REST HTTP write action."""
        builder = RequestBodyBuilder(action.mappings, action.static_values)
        body = builder.build(data)

        path_resolver = PathResolver()
        resolved_path = path_resolver.resolve(action.endpoint_path, data, endpoint.parameters)

        auth_value = await self.vault.get(auth_ref)
        headers: dict[str, str] = {"Authorization": f"Bearer {auth_value}"}

        idem_key = idempotency_key
        if endpoint.idempotency_header:
            idem_key = idempotency_key or uuid4().hex
            headers[endpoint.idempotency_header] = idem_key

        url = f"{schema.source_url.rstrip('/')}{resolved_path}"

        rate_key = self._rate_key(action.endpoint_path)

        async def _send() -> httpx.Response:
            if self.rate_limiter is not None and self.respect_rate_limit:
                await self.rate_limiter.acquire(rate_key)
            resp = await self.http_client.request(
                method=action.endpoint_method,
                url=url,
                json=body,
                headers=headers,
            )
            if self.rate_limiter is not None:
                await self.rate_limiter.observe_response(rate_key, resp)
            if resp.status_code == 429:
                retry_after = resp.headers.get("retry-after")
                raise RateLimitError(
                    f"Rate limited: {resp.text[:200]}",
                    retry_after=float(retry_after) if retry_after else None,
                )
            if resp.status_code >= 500:
                raise ServiceDownError(f"Server error ({resp.status_code}): {resp.text[:200]}")
            return resp

        try:
            response = await with_retry(_send, self.retry_policy)
        except (RateLimitError, ServiceDownError) as exc:
            return ActionResult(
                action_id=action.action_id,
                endpoint_path=action.endpoint_path,
                method=action.endpoint_method,
                status_code=429 if isinstance(exc, RateLimitError) else 500,
                success=False,
                error=_action_error_for_exception(exc),
                idempotency_key=idem_key,
            )

        if response.is_success:
            try:
                resp_body = response.json()
            except Exception:
                resp_body = None
            return ActionResult(
                action_id=action.action_id,
                endpoint_path=action.endpoint_path,
                method=action.endpoint_method,
                status_code=response.status_code,
                success=True,
                response_body=resp_body,
                idempotency_key=idem_key,
            )

        return ActionResult(
            action_id=action.action_id,
            endpoint_path=action.endpoint_path,
            method=action.endpoint_method,
            status_code=response.status_code,
            success=False,
            error=_action_error_for_status(response.status_code, response.text[:500]),
            idempotency_key=idem_key,
        )

    async def _execute_graphql(
        self,
        action: ActionConfig,
        data: dict[str, Any],
        schema: APISchema,
        auth_ref: str,
        endpoint: Endpoint,
        idempotency_key: str | None = None,
    ) -> ActionResult:
        """Execute a GraphQL mutation via HTTP POST to /graphql."""
        match = _GRAPHQL_MUTATION_RE.match(action.endpoint_path)
        mutation_name = match.group(1) if match else "unknown"

        # Build variables from action mappings
        builder = RequestBodyBuilder(action.mappings, action.static_values)
        variables = builder.build(data)

        # Build argument signature and variable definitions from the variables dict
        var_defs, args = _build_graphql_args(variables)

        # Build a simple selection set from response_schema
        selection = _build_selection_set(endpoint.response_schema)

        query = f"mutation{var_defs} {{ {mutation_name}{args} {selection} }}"

        graphql_body = {"query": query, "variables": _flatten_variables(variables)}

        auth_value = await self.vault.get(auth_ref)
        headers: dict[str, str] = {
            "Authorization": f"Bearer {auth_value}",
            "Content-Type": "application/json",
        }

        idem_key = idempotency_key
        if endpoint.idempotency_header:
            idem_key = idempotency_key or uuid4().hex
            headers[endpoint.idempotency_header] = idem_key

        # GraphQL endpoint is always /graphql (strip the #mutation.xxx fragment)
        graphql_path = action.endpoint_path.split("#")[0]
        url = f"{schema.source_url.rstrip('/')}{graphql_path}"

        rate_key = self._rate_key(action.endpoint_path)

        async def _send() -> httpx.Response:
            if self.rate_limiter is not None and self.respect_rate_limit:
                await self.rate_limiter.acquire(rate_key)
            resp = await self.http_client.request(
                method="POST",
                url=url,
                json=graphql_body,
                headers=headers,
            )
            if self.rate_limiter is not None:
                await self.rate_limiter.observe_response(rate_key, resp)
            if resp.status_code == 429:
                retry_after = resp.headers.get("retry-after")
                raise RateLimitError(
                    f"Rate limited: {resp.text[:200]}",
                    retry_after=float(retry_after) if retry_after else None,
                )
            if resp.status_code >= 500:
                raise ServiceDownError(f"Server error ({resp.status_code}): {resp.text[:200]}")
            return resp

        try:
            response = await with_retry(_send, self.retry_policy)
        except (RateLimitError, ServiceDownError) as exc:
            return ActionResult(
                action_id=action.action_id,
                endpoint_path=action.endpoint_path,
                method=action.endpoint_method,
                status_code=429 if isinstance(exc, RateLimitError) else 500,
                success=False,
                error=_action_error_for_exception(exc),
                idempotency_key=idem_key,
            )

        try:
            resp_body = response.json()
        except Exception:
            resp_body = None

        # GraphQL can return 200 with errors in the body
        if response.is_success and resp_body:
            gql_errors = resp_body.get("errors")
            if gql_errors:
                return ActionResult(
                    action_id=action.action_id,
                    endpoint_path=action.endpoint_path,
                    method=action.endpoint_method,
                    status_code=response.status_code,
                    success=False,
                    error=ActionError(
                        type=ActionErrorType.VALIDATION_ERROR,
                        message=gql_errors[0].get("message", "GraphQL error"),
                        details={"errors": gql_errors},
                        recovery_hint="GraphQL mutation returned errors — check field arguments against schema",
                    ),
                    idempotency_key=idem_key,
                )

            return ActionResult(
                action_id=action.action_id,
                endpoint_path=action.endpoint_path,
                method=action.endpoint_method,
                status_code=response.status_code,
                success=True,
                response_body=resp_body.get("data", resp_body),
                idempotency_key=idem_key,
            )

        gql_message = (resp_body or {}).get("message", response.text[:500]) if resp_body else response.text[:500]
        return ActionResult(
            action_id=action.action_id,
            endpoint_path=action.endpoint_path,
            method=action.endpoint_method,
            status_code=response.status_code,
            success=False,
            error=_action_error_for_status(response.status_code, gql_message),
            idempotency_key=idem_key,
        )

    async def _execute_mcp(
        self,
        action: ActionConfig,
        data: dict[str, Any],
        schema: APISchema,
        endpoint: Endpoint,
    ) -> ActionResult:
        """Execute an MCP tool call, using the MCP SDK if available, else HTTP POST."""
        match = _MCP_TOOL_RE.match(action.endpoint_path)
        tool_name = match.group(1) if match else "unknown"

        # Build arguments from action mappings
        builder = RequestBodyBuilder(action.mappings, action.static_values)
        arguments = builder.build(data)

        # Try native MCP SDK first
        try:
            from mcp import ClientSession  # type: ignore[import-untyped]
            from mcp.client.streamable_http import streamable_http_client  # type: ignore[import-untyped]

            return await self._execute_mcp_native(
                action, tool_name, arguments, schema, ClientSession, streamable_http_client
            )
        except ImportError:
            logger.debug("MCP SDK not available, falling back to HTTP POST for tool %s", tool_name)
            return await self._execute_mcp_http(action, tool_name, arguments, schema, endpoint)

    async def _execute_mcp_native(
        self,
        action: ActionConfig,
        tool_name: str,
        arguments: dict[str, Any],
        schema: APISchema,
        client_session_cls: type,
        streamable_http_client_fn: Any,
    ) -> ActionResult:
        """Execute MCP tool via native SDK session.call_tool()."""
        mcp_url = f"{schema.source_url.rstrip('/')}/mcp"

        try:
            async with streamable_http_client_fn(mcp_url) as (read, write), client_session_cls(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)

                # MCP call_tool returns a result object with content
                content = getattr(result, "content", None)
                is_error = getattr(result, "isError", False)

                resp_body: dict[str, Any] | None = None
                if content:
                    # content is a list of content items; extract text
                    texts = [getattr(c, "text", str(c)) for c in content]
                    combined = texts[0] if len(texts) == 1 else "\n".join(texts)
                    try:
                        resp_body = _json.loads(combined)
                    except (ValueError, TypeError):
                        resp_body = {"result": combined}

                if is_error:
                    return ActionResult(
                        action_id=action.action_id,
                        endpoint_path=action.endpoint_path,
                        method=action.endpoint_method,
                        status_code=400,
                        success=False,
                        error=ActionError(
                            type=ActionErrorType.SERVER_ERROR,
                            message=resp_body.get("result", "MCP tool error") if resp_body else "MCP tool error",
                            recovery_hint="MCP tool returned an error — verify tool arguments",
                        ),
                    )

                return ActionResult(
                    action_id=action.action_id,
                    endpoint_path=action.endpoint_path,
                    method=action.endpoint_method,
                    status_code=200,
                    success=True,
                    response_body=resp_body,
                )
        except Exception as exc:
            logger.warning("MCP native call failed for %s: %s", tool_name, exc)
            return ActionResult(
                action_id=action.action_id,
                endpoint_path=action.endpoint_path,
                method=action.endpoint_method,
                status_code=500,
                success=False,
                error=ActionError(
                    type=ActionErrorType.SERVER_ERROR,
                    message=f"MCP tool call failed: {exc}",
                    recovery_hint="MCP tool call failed — check MCP server connectivity and tool availability",
                ),
            )

    async def _execute_mcp_http(
        self,
        action: ActionConfig,
        tool_name: str,
        arguments: dict[str, Any],
        schema: APISchema,
        endpoint: Endpoint,
    ) -> ActionResult:
        """Fallback: execute MCP tool via HTTP POST."""
        url = f"{schema.source_url.rstrip('/')}/mcp/tools/{tool_name}"

        rate_key = self._rate_key(action.endpoint_path)

        async def _send() -> httpx.Response:
            if self.rate_limiter is not None and self.respect_rate_limit:
                await self.rate_limiter.acquire(rate_key)
            resp = await self.http_client.request(
                method="POST",
                url=url,
                json=arguments,
                headers={"Content-Type": "application/json"},
            )
            if self.rate_limiter is not None:
                await self.rate_limiter.observe_response(rate_key, resp)
            if resp.status_code == 429:
                retry_after = resp.headers.get("retry-after")
                raise RateLimitError(
                    f"Rate limited: {resp.text[:200]}",
                    retry_after=float(retry_after) if retry_after else None,
                )
            if resp.status_code >= 500:
                raise ServiceDownError(f"Server error ({resp.status_code}): {resp.text[:200]}")
            return resp

        try:
            response = await with_retry(_send, self.retry_policy)
        except (RateLimitError, ServiceDownError) as exc:
            return ActionResult(
                action_id=action.action_id,
                endpoint_path=action.endpoint_path,
                method=action.endpoint_method,
                status_code=429 if isinstance(exc, RateLimitError) else 500,
                success=False,
                error=_action_error_for_exception(exc),
            )

        if response.is_success:
            try:
                resp_body = response.json()
            except Exception:
                resp_body = None
            return ActionResult(
                action_id=action.action_id,
                endpoint_path=action.endpoint_path,
                method=action.endpoint_method,
                status_code=response.status_code,
                success=True,
                response_body=resp_body,
            )

        return ActionResult(
            action_id=action.action_id,
            endpoint_path=action.endpoint_path,
            method=action.endpoint_method,
            status_code=response.status_code,
            success=False,
            error=_action_error_for_status(response.status_code, response.text[:500]),
        )


def _build_graphql_args(variables: dict[str, Any]) -> tuple[str, str]:
    """Build GraphQL variable definitions and argument references.

    Given variables like {"input": {"title": "x", "price": 10}},
    produces:
      var_defs: "($input: JSON!)" — simplified; we pass as JSON variables
      args:     "(input: $input)"

    For flat variables like {"title": "x", "price": 10}:
      var_defs: "($title: JSON!, $price: JSON!)"
      args:     "(title: $title, price: $price)"
    """
    if not variables:
        return "", ""

    flat = _flatten_variables(variables)
    var_def_parts = [f"${k}: JSON" for k in flat]
    arg_parts = [f"{k}: ${k}" for k in flat]

    var_defs = f"({', '.join(var_def_parts)})"
    args = f"({', '.join(arg_parts)})"
    return var_defs, args


def _flatten_variables(variables: dict[str, Any]) -> dict[str, Any]:
    """Flatten nested variable dicts for GraphQL variable passing.

    For top-level keys, keeps them as-is. The actual nested values are
    passed as GraphQL JSON variables which handles complex inputs.
    """
    return variables


def _build_selection_set(response_schema: dict[str, Any]) -> str:
    """Build a minimal GraphQL selection set from response schema."""
    if not response_schema:
        return "{ __typename }"

    title = response_schema.get("title", "")
    schema_type = response_schema.get("type", "")

    if schema_type == "array":
        items = response_schema.get("items", {})
        return _build_selection_set(items)

    if schema_type == "object" and title:
        return "{ __typename }"

    return "{ __typename }"
