"""Transport abstraction — one driver per wire protocol.

The :class:`~liquid.sync.fetcher.Fetcher` owns the cross-cutting concerns that
are the same for every protocol (caching, rate-limit accounting, telemetry,
evolution signals, pagination orchestration) and delegates the actual wire call
to a :class:`ProtocolDriver` selected by ``Endpoint.protocol``.

A driver's job is narrow: given a :class:`FetchContext` (a fully-prepared
request — params, headers, built auth, cursor), perform the call and return a
:class:`DriverResponse` with the *normalized* result — a status code, a header
mapping, and the extracted records. Drivers never raise on protocol-level error
statuses; they report them in the response and let the Fetcher map them to the
shared recovery exceptions. This keeps non-HTTP protocols first-class instead of
bolted onto an HTTP-shaped pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import httpx

    from liquid.auth.schemes import AuthScheme
    from liquid.models.schema import Endpoint
    from liquid.protocols import Vault
    from liquid.sync.pagination import PaginationStrategy
    from liquid.sync.selector import RecordSelector


@dataclass(slots=True)
class DriverResponse:
    """Normalized result of one wire call, protocol-agnostic.

    ``status_code`` and ``headers`` are normalized so the Fetcher's shared
    rate-limit / telemetry / error-mapping logic works regardless of protocol
    (a driver maps its native status onto HTTP-like codes — e.g. a gRPC
    ``UNAUTHENTICATED`` → 401, ``NOT_FOUND`` → 404). ``records`` is populated
    only on success; on an error status the driver leaves it empty and sets
    ``error_body`` with a short diagnostic string. ``raw`` carries the
    underlying response object when one exists (an :class:`httpx.Response` for
    HTTP-shaped protocols) so the Fetcher can preserve exact HTTP behaviour and
    feed evolution-signal extraction.
    """

    status_code: int
    headers: dict[str, str] = field(default_factory=dict)
    records: list[dict[str, Any]] = field(default_factory=list)
    next_cursor: str | None = None
    error_body: str | None = None
    raw: Any | None = None


@dataclass(slots=True)
class FetchContext:
    """Everything a driver needs to perform one call.

    The Fetcher prepares ``params`` (pagination + caller extras already merged),
    ``headers``, and the built ``auth`` object before handing the context over,
    so most drivers only build a request and parse the response. ``vault`` /
    ``auth_ref`` / ``auth_scheme`` are exposed for protocols whose auth doesn't
    ride an :class:`httpx.Auth` (e.g. gRPC call metadata).
    """

    endpoint: Endpoint
    base_url: str
    params: dict[str, Any]
    headers: dict[str, str]
    cursor: str | None
    selector: RecordSelector
    pagination: PaginationStrategy
    vault: Vault
    auth_ref: str
    auth: httpx.Auth | None = None
    auth_scheme: AuthScheme | None = None
    http_client: httpx.AsyncClient | None = None


@dataclass(slots=True)
class WriteContext:
    """Everything a driver needs to perform one write (INSERT / UPDATE / DELETE).

    ``op`` is ``"insert"`` | ``"update"`` | ``"delete"``. ``values`` are the row
    fields to write (insert/update); ``where`` selects rows (update/delete). The
    driver resolves its own connection (DSN from the vault, like on read) and
    validates columns against the endpoint's ``transport_meta`` before building a
    parameterized statement. Writes are gated and opt-in at the client layer.
    """

    endpoint: Endpoint
    base_url: str
    op: str
    values: dict[str, Any]
    where: dict[str, Any]
    vault: Vault
    auth_ref: str
    auth: httpx.Auth | None = None
    http_client: httpx.AsyncClient | None = None
    idempotency_key: str | None = None


@dataclass(slots=True)
class SenseEvent:
    """One perceived signal from the world — the afferent counterpart of a write.

    Deliberately **modality-agnostic**: ``modality`` tags what kind of signal this
    is (``"data"`` for a row/record/message — the default today; later ``"audio"``,
    ``"telemetry"``, ``"scent"``, … as agents grow new senses), and ``payload`` is
    an open dict so a new modality needs no schema change. ``source`` identifies
    where it came from (endpoint path / channel); ``cursor`` lets a consumer resume
    a stream where it left off.
    """

    source: str
    payload: dict[str, Any]
    modality: str = "data"
    cursor: str | None = None


@dataclass(slots=True)
class SenseContext:
    """Everything a driver needs to perceive a stream of events from an endpoint.

    Like :class:`FetchContext` but for the *continuous* afferent direction. The
    driver resolves its own connection (DSN from the vault, as on read) and yields
    :class:`SenseEvent`s until the bounds are hit. ``cursor`` resumes a delta
    source; ``poll_interval`` paces pollers; ``max_events`` / ``max_seconds`` bound
    the stream so a caller can't block forever (``None`` = unbounded).
    """

    endpoint: Endpoint
    base_url: str
    params: dict[str, Any]
    vault: Vault
    auth_ref: str
    cursor: str | None = None
    poll_interval: float = 2.0
    max_events: int | None = None
    max_seconds: float | None = None
    auth: httpx.Auth | None = None
    http_client: httpx.AsyncClient | None = None


@runtime_checkable
class ProtocolDriver(Protocol):
    """Performs a single wire call for one protocol."""

    scheme: str

    async def fetch(self, ctx: FetchContext) -> DriverResponse: ...


@runtime_checkable
class WriteDriver(Protocol):
    """A driver that can also write. Optional — most wire protocols are read-only here.

    Drivers implement this in addition to :class:`ProtocolDriver`; the client
    checks ``isinstance(driver, WriteDriver)`` and refuses the write otherwise.
    """

    scheme: str

    async def write(self, ctx: WriteContext) -> DriverResponse: ...


def supports_write(driver: object) -> bool:
    """Whether ``driver`` can perform writes (implements :class:`WriteDriver`)."""
    return isinstance(driver, WriteDriver)


@runtime_checkable
class SenseDriver(Protocol):
    """A driver that can *perceive* — yield a live stream of events from an endpoint.

    Optional, like :class:`WriteDriver`. Drivers implement it in addition to
    :class:`ProtocolDriver`; the client checks ``isinstance(driver, SenseDriver)``
    and declines otherwise. This is the afferent organ — the agent's senses — to
    ``write``'s efferent hands.
    """

    scheme: str

    def sense(self, ctx: SenseContext) -> AsyncIterator[SenseEvent]: ...


def supports_sense(driver: object) -> bool:
    """Whether ``driver`` can perceive (implements :class:`SenseDriver`)."""
    return isinstance(driver, SenseDriver)


_REGISTRY: dict[str, ProtocolDriver] = {}


def register_driver(driver: ProtocolDriver) -> None:
    """Register a driver under its ``scheme``. Idempotent (last wins)."""
    _REGISTRY[driver.scheme] = driver


def get_driver(protocol: str | None) -> ProtocolDriver:
    """Look up the driver for a protocol, falling back to HTTP.

    Unknown/empty protocols resolve to the HTTP driver so an adapter authored
    before a given driver existed still fetches as REST rather than failing.
    """
    if protocol and protocol in _REGISTRY:
        return _REGISTRY[protocol]
    return _REGISTRY["http"]
