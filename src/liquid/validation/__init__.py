"""Response-shape validation — catch semantic drift before it propagates.

HTTP recovery (0.12) handles 401/404/410/429/5xx. Evolution (0.22) handles
provider-announced deprecation. This module handles the quiet case: the
response returns 200 OK with a valid envelope, but individual fields an
adapter was discovered against have gone missing, changed type, or turned
unexpectedly null.

Design mirrors evolution signals: validation runs after ``RecordMapper``,
emits :class:`SchemaMismatchSignal` objects, fires
:attr:`Liquid.on_schema_mismatch`, and (when ``include_meta=True``) lands
in ``_meta.validation``. It never raises — a degraded response is still
better than a missed one, and the caller decides whether to hard-fail.
"""

from liquid.validation.validator import (
    MismatchKind,
    ResponseValidator,
    SchemaMismatchSignal,
)

__all__ = [
    "MismatchKind",
    "ResponseValidator",
    "SchemaMismatchSignal",
]
