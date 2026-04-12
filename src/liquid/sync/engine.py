from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from liquid.events import ReDiscoveryNeeded, SyncCompleted, SyncFailed
from liquid.exceptions import SyncRuntimeError
from liquid.models import SyncResult
from liquid.models.sync import SyncError, SyncErrorType
from liquid.sync.fetcher import Fetcher  # noqa: TC001
from liquid.sync.mapper import RecordMapper  # noqa: TC001
from liquid.sync.retry import RetryPolicy, with_retry

if TYPE_CHECKING:
    from liquid.events import EventHandler
    from liquid.models.adapter import AdapterConfig
    from liquid.models.schema import Endpoint
    from liquid.protocols import DataSink

logger = logging.getLogger(__name__)


class SyncEngine:
    def __init__(
        self,
        fetcher: Fetcher,
        mapper: RecordMapper,
        sink: DataSink,
        event_handler: EventHandler | None = None,
        retry_policy: RetryPolicy | None = None,
        failure_threshold: int = 5,
    ) -> None:
        self.fetcher = fetcher
        self.mapper = mapper
        self.sink = sink
        self.event_handler = event_handler
        self.retry_policy = retry_policy or RetryPolicy()
        self.failure_threshold = failure_threshold
        self._consecutive_failures = 0

    async def run(self, config: AdapterConfig, cursor: str | None = None) -> SyncResult:
        started_at = datetime.now(UTC)
        total_fetched = 0
        total_mapped = 0
        total_delivered = 0
        errors: list[SyncError] = []
        current_cursor = cursor

        for ep_path in config.sync.endpoints:
            endpoint = self._find_endpoint(config, ep_path)
            if endpoint is None:
                errors.append(
                    SyncError(
                        type=SyncErrorType.ENDPOINT_GONE,
                        message=f"Endpoint {ep_path} not found in schema",
                        endpoint=ep_path,
                    )
                )
                continue

            try:
                page_cursor = current_cursor
                while True:
                    fetch_result = await with_retry(
                        lambda ep=endpoint, c=page_cursor: self.fetcher.fetch(
                            endpoint=ep,
                            base_url=config.schema_.source_url,
                            auth_ref=config.auth_ref,
                            cursor=c,
                        ),
                        self.retry_policy,
                    )

                    total_fetched += len(fetch_result.records)

                    mapped_records = self.mapper.map_batch(fetch_result.records, ep_path)
                    total_mapped += len(mapped_records)

                    delivery = await self.sink.deliver(mapped_records)
                    total_delivered += delivery.delivered

                    if delivery.errors:
                        for err_msg in delivery.errors:
                            errors.append(
                                SyncError(
                                    type=SyncErrorType.DELIVERY_ERROR,
                                    message=err_msg,
                                    endpoint=ep_path,
                                )
                            )

                    page_cursor = fetch_result.next_cursor
                    current_cursor = page_cursor
                    if page_cursor is None:
                        break

            except SyncRuntimeError as e:
                error_type = _classify_error(e)
                errors.append(
                    SyncError(
                        type=error_type,
                        message=str(e),
                        endpoint=ep_path,
                    )
                )

        result = SyncResult(
            adapter_id=config.config_id,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            records_fetched=total_fetched,
            records_mapped=total_mapped,
            records_delivered=total_delivered,
            errors=errors,
            next_cursor=current_cursor,
        )

        await self._handle_result(result, config)
        return result

    async def _handle_result(self, result: SyncResult, config: AdapterConfig) -> None:
        if not self.event_handler:
            return

        if result.errors:
            self._consecutive_failures += 1
            await self.event_handler.handle(
                SyncFailed(
                    adapter_id=config.config_id,
                    error=result.errors[-1],
                    consecutive_failures=self._consecutive_failures,
                )
            )
            if self._consecutive_failures >= self.failure_threshold:
                failures = self._consecutive_failures
                threshold = self.failure_threshold
                await self.event_handler.handle(
                    ReDiscoveryNeeded(
                        adapter_id=config.config_id,
                        reason=f"Consecutive failures ({failures}) exceeded threshold ({threshold})",
                    )
                )
        else:
            self._consecutive_failures = 0
            await self.event_handler.handle(
                SyncCompleted(
                    adapter_id=config.config_id,
                    result=result,
                )
            )

    @staticmethod
    def _find_endpoint(config: AdapterConfig, path: str) -> Endpoint | None:
        for ep in config.schema_.endpoints:
            if ep.path == path:
                return ep
        return None


def _classify_error(exc: SyncRuntimeError) -> SyncErrorType:
    from liquid.exceptions import AuthError, EndpointGoneError, FieldNotFoundError, RateLimitError, ServiceDownError

    match exc:
        case FieldNotFoundError():
            return SyncErrorType.FIELD_NOT_FOUND
        case AuthError():
            return SyncErrorType.AUTH_ERROR
        case RateLimitError():
            return SyncErrorType.RATE_LIMIT
        case ServiceDownError():
            return SyncErrorType.SERVICE_DOWN
        case EndpointGoneError():
            return SyncErrorType.ENDPOINT_GONE
        case _:
            return SyncErrorType.FIELD_NOT_FOUND
