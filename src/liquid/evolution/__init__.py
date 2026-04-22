"""Schema evolution — detect provider-side deprecation + version drift.

Three library-side signals that don't need cloud infrastructure:

  * ``Deprecation`` HTTP header (RFC 9745) — provider announces an
    endpoint/field will be removed.
  * ``Sunset`` HTTP header (RFC 8594) — provider gives the removal date.
  * ``API-Version`` response header — compare against the version the
    adapter was discovered against; mismatch is early warning of drift.

All three surface through ``EvolutionSignal``, which the fetcher attaches
to :class:`~liquid.meta.FetchMeta` under ``_meta.evolution``. Handlers
can also register an ``on_evolution`` callback on :class:`Liquid` to
react in real time (emit metrics, page oncall, stash the signal).

Cloud-side ``schema_history`` snapshots are deferred — this is the
synchronous-per-response piece that works locally.
"""

from liquid.evolution.signals import (
    EvolutionKind,
    EvolutionSignal,
    extract_signals,
)

__all__ = [
    "EvolutionKind",
    "EvolutionSignal",
    "extract_signals",
]
