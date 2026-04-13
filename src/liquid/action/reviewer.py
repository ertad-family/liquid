from __future__ import annotations

from typing import TYPE_CHECKING

from liquid.mapping.reviewer import MappingStatus

if TYPE_CHECKING:
    from liquid.models.action import ActionMapping


class ActionReview:
    """Manages the human review workflow for proposed action mappings."""

    def __init__(self, proposals: list[ActionMapping]) -> None:
        self._proposed = list(proposals)
        self._statuses: list[MappingStatus] = [MappingStatus.PENDING] * len(proposals)
        self._corrections: list[ActionMapping | None] = [None] * len(proposals)

    @property
    def proposed(self) -> list[ActionMapping]:
        return list(self._proposed)

    def __len__(self) -> int:
        return len(self._proposed)

    def approve(self, index: int) -> None:
        self._check_index(index)
        self._statuses[index] = MappingStatus.APPROVED

    def reject(self, index: int) -> None:
        self._check_index(index)
        self._statuses[index] = MappingStatus.REJECTED

    def correct(self, index: int, corrected: ActionMapping) -> None:
        self._check_index(index)
        self._corrections[index] = corrected
        self._statuses[index] = MappingStatus.CORRECTED

    def approve_all(self) -> None:
        for i in range(len(self._statuses)):
            if self._statuses[i] == MappingStatus.PENDING:
                self._statuses[i] = MappingStatus.APPROVED

    def finalize(self) -> list[ActionMapping]:
        """Return only approved and corrected mappings."""
        result: list[ActionMapping] = []
        for i, status in enumerate(self._statuses):
            if status == MappingStatus.APPROVED:
                result.append(self._proposed[i])
            elif status == MappingStatus.CORRECTED and self._corrections[i]:
                result.append(self._corrections[i])
        return result

    def corrections(self) -> list[tuple[ActionMapping, ActionMapping]]:
        """Return (original, corrected) pairs for learning."""
        pairs: list[tuple[ActionMapping, ActionMapping]] = []
        for i, status in enumerate(self._statuses):
            if status == MappingStatus.CORRECTED and self._corrections[i]:
                pairs.append((self._proposed[i], self._corrections[i]))
        return pairs

    def status(self, index: int) -> MappingStatus:
        self._check_index(index)
        return self._statuses[index]

    def _check_index(self, index: int) -> None:
        if index < 0 or index >= len(self._proposed):
            raise IndexError(f"Action mapping index {index} out of range (0-{len(self._proposed) - 1})")
