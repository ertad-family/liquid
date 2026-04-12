from __future__ import annotations

from enum import StrEnum

from liquid.models.adapter import FieldMapping


class MappingStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CORRECTED = "corrected"


class MappingReview:
    """Manages the human review workflow for proposed field mappings."""

    def __init__(self, proposed: list[FieldMapping]) -> None:
        self._proposed = list(proposed)
        self._statuses: list[MappingStatus] = [MappingStatus.PENDING] * len(proposed)
        self._corrections: list[FieldMapping | None] = [None] * len(proposed)

    @property
    def proposed(self) -> list[FieldMapping]:
        return list(self._proposed)

    def __len__(self) -> int:
        return len(self._proposed)

    def approve(self, index: int) -> None:
        self._check_index(index)
        self._statuses[index] = MappingStatus.APPROVED

    def reject(self, index: int) -> None:
        self._check_index(index)
        self._statuses[index] = MappingStatus.REJECTED

    def correct(
        self,
        index: int,
        target_field: str | None = None,
        transform: str | None = None,
        source_path: str | None = None,
    ) -> None:
        self._check_index(index)
        original = self._proposed[index]
        self._corrections[index] = FieldMapping(
            source_path=source_path or original.source_path,
            target_field=target_field or original.target_field,
            transform=transform if transform is not None else original.transform,
            confidence=1.0,
        )
        self._statuses[index] = MappingStatus.CORRECTED

    def approve_all(self) -> None:
        for i in range(len(self._statuses)):
            if self._statuses[i] == MappingStatus.PENDING:
                self._statuses[i] = MappingStatus.APPROVED

    def finalize(self) -> list[FieldMapping]:
        """Return only approved and corrected mappings."""
        result: list[FieldMapping] = []
        for i, status in enumerate(self._statuses):
            if status == MappingStatus.APPROVED:
                result.append(self._proposed[i])
            elif status == MappingStatus.CORRECTED and self._corrections[i]:
                result.append(self._corrections[i])
        return result

    def corrections(self) -> list[tuple[FieldMapping, FieldMapping]]:
        """Return (original, corrected) pairs for learning."""
        pairs: list[tuple[FieldMapping, FieldMapping]] = []
        for i, status in enumerate(self._statuses):
            if status == MappingStatus.CORRECTED and self._corrections[i]:
                pairs.append((self._proposed[i], self._corrections[i]))
        return pairs

    def status(self, index: int) -> MappingStatus:
        self._check_index(index)
        return self._statuses[index]

    def _check_index(self, index: int) -> None:
        if index < 0 or index >= len(self._proposed):
            raise IndexError(f"Mapping index {index} out of range (0-{len(self._proposed) - 1})")
