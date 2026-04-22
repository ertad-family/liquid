"""Response validator — field-coverage + type-mismatch detection."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from liquid.exceptions import Recovery, ToolCall

if TYPE_CHECKING:
    from liquid.models.adapter import FieldMapping


class MismatchKind(StrEnum):
    FIELD_MISSING = "field_missing"
    """A declared mapping target is null/absent in >= threshold of records."""

    TYPE_MISMATCH = "type_mismatch"
    """The observed value type doesn't match what the adapter was built against."""


class SchemaMismatchSignal(BaseModel):
    """One validation finding. Severity escalates with prevalence."""

    kind: MismatchKind
    severity: str = "warn"  # "info" | "warn" | "critical"
    target_field: str
    source_path: str | None = None
    endpoint: str | None = None
    coverage: float
    """Fraction of records where the field was present with the expected type."""
    observed_type: str | None = None
    expected_type: str | None = None
    sample_size: int
    message: str
    recovery: Recovery | None = None
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


_EXPECTED_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "str": (str,),
    "string": (str,),
    "int": (int,),
    "integer": (int,),
    "float": (float, int),
    "number": (float, int),
    "bool": (bool,),
    "boolean": (bool,),
    "list": (list,),
    "array": (list,),
    "dict": (dict,),
    "object": (dict,),
}


def _is_type_compatible(value: Any, expected: str) -> bool:
    exp = expected.lower().strip()
    types = _EXPECTED_TYPE_MAP.get(exp)
    if types is None:
        return True  # unknown expected type — don't flag
    # bool is a subclass of int; reject silently.
    if exp in {"int", "integer"} and isinstance(value, bool):
        return False
    return isinstance(value, types)


class ResponseValidator:
    """Validates mapped record batches against the adapter's field mappings.

    Configure once per fetch; call :meth:`validate` to get the list of
    signals. No state is retained between calls.
    """

    def __init__(
        self,
        mappings: list[FieldMapping],
        *,
        coverage_threshold: float = 0.9,
        type_hints: dict[str, str] | None = None,
    ) -> None:
        self._mappings = mappings
        self._threshold = coverage_threshold
        self._type_hints = type_hints or {}

    def validate(
        self,
        records: list[dict[str, Any]],
        *,
        endpoint: str | None = None,
    ) -> list[SchemaMismatchSignal]:
        if not records or not self._mappings:
            return []

        signals: list[SchemaMismatchSignal] = []
        sample_size = len(records)

        for mapping in self._mappings:
            target = mapping.target_field
            present_count = 0
            type_hit_count = 0
            first_bad_type: str | None = None

            expected_type = self._type_hints.get(target)

            for record in records:
                if target not in record:
                    continue
                value = record[target]
                if value is None:
                    continue
                present_count += 1
                if expected_type is None:
                    continue
                if _is_type_compatible(value, expected_type):
                    type_hit_count += 1
                elif first_bad_type is None:
                    first_bad_type = type(value).__name__

            coverage = present_count / sample_size
            if coverage < self._threshold:
                severity = "critical" if coverage < 0.5 else "warn"
                signals.append(
                    SchemaMismatchSignal(
                        kind=MismatchKind.FIELD_MISSING,
                        severity=severity,
                        target_field=target,
                        source_path=mapping.source_path,
                        endpoint=endpoint,
                        coverage=round(coverage, 3),
                        sample_size=sample_size,
                        expected_type=expected_type,
                        message=(
                            f"Field '{target}' present in only "
                            f"{present_count}/{sample_size} records "
                            f"({coverage:.0%}) — provider may have renamed or removed it"
                        ),
                        recovery=Recovery(
                            hint=(
                                f"Re-run discovery against {endpoint or 'the adapter'} — "
                                f"source_path '{mapping.source_path}' may have moved"
                            ),
                            next_action=ToolCall(
                                tool="rediscover_adapter",
                                args={
                                    "endpoint": endpoint,
                                    "field": target,
                                    "source_path": mapping.source_path,
                                },
                                description=(
                                    "Re-discover the adapter schema to find the new location of the missing field."
                                ),
                            ),
                            retry_safe=False,
                        ),
                    )
                )
                continue  # type check on a missing field is noise

            if expected_type and present_count > 0:
                type_coverage = type_hit_count / present_count
                if type_coverage < self._threshold:
                    severity = "critical" if type_coverage < 0.5 else "warn"
                    signals.append(
                        SchemaMismatchSignal(
                            kind=MismatchKind.TYPE_MISMATCH,
                            severity=severity,
                            target_field=target,
                            source_path=mapping.source_path,
                            endpoint=endpoint,
                            coverage=round(type_coverage, 3),
                            sample_size=present_count,
                            expected_type=expected_type,
                            observed_type=first_bad_type,
                            message=(
                                f"Field '{target}' has type {first_bad_type} in "
                                f"{present_count - type_hit_count}/{present_count} "
                                f"records (expected {expected_type}) — type contract broken"
                            ),
                            recovery=Recovery(
                                hint=(f"Re-run discovery — provider may have changed the type of '{target}'"),
                                next_action=ToolCall(
                                    tool="rediscover_adapter",
                                    args={
                                        "endpoint": endpoint,
                                        "field": target,
                                        "observed_type": first_bad_type,
                                        "expected_type": expected_type,
                                    },
                                    description=(
                                        "Re-discover to capture the new field type. "
                                        "Existing mappings may need a transform."
                                    ),
                                ),
                                retry_safe=False,
                            ),
                        )
                    )

        return signals
