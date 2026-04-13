import pytest

from liquid.action.reviewer import ActionReview
from liquid.mapping.reviewer import MappingStatus
from liquid.models.action import ActionMapping


def _make_action_mappings() -> list[ActionMapping]:
    return [
        ActionMapping(source_field="amount", target_path="total_price", confidence=0.8),
        ActionMapping(source_field="email", target_path="customer.email", confidence=0.9),
        ActionMapping(source_field="note", target_path="order_note", confidence=0.7),
    ]


class TestActionReview:
    def test_initial_state(self):
        review = ActionReview(_make_action_mappings())
        assert len(review) == 3
        assert review.status(0) == MappingStatus.PENDING
        assert review.status(1) == MappingStatus.PENDING

    def test_approve(self):
        review = ActionReview(_make_action_mappings())
        review.approve(0)
        assert review.status(0) == MappingStatus.APPROVED

    def test_reject(self):
        review = ActionReview(_make_action_mappings())
        review.reject(1)
        assert review.status(1) == MappingStatus.REJECTED

    def test_correct(self):
        review = ActionReview(_make_action_mappings())
        corrected = ActionMapping(source_field="total", target_path="total_price", confidence=1.0)
        review.correct(0, corrected)
        assert review.status(0) == MappingStatus.CORRECTED

    def test_approve_all(self):
        review = ActionReview(_make_action_mappings())
        review.reject(1)
        review.approve_all()
        assert review.status(0) == MappingStatus.APPROVED
        assert review.status(1) == MappingStatus.REJECTED  # not overridden
        assert review.status(2) == MappingStatus.APPROVED

    def test_finalize_approved_only(self):
        review = ActionReview(_make_action_mappings())
        review.approve(0)
        review.reject(1)
        review.approve(2)
        result = review.finalize()
        assert len(result) == 2
        assert result[0].source_field == "amount"
        assert result[1].source_field == "note"

    def test_finalize_includes_corrections(self):
        review = ActionReview(_make_action_mappings())
        review.approve(0)
        corrected = ActionMapping(source_field="user_email", target_path="customer.email", confidence=1.0)
        review.correct(1, corrected)
        review.reject(2)
        result = review.finalize()
        assert len(result) == 2
        assert result[1].source_field == "user_email"
        assert result[1].confidence == 1.0

    def test_corrections_returns_pairs(self):
        review = ActionReview(_make_action_mappings())
        corrected = ActionMapping(source_field="total", target_path="total_price", confidence=1.0)
        review.correct(0, corrected)
        pairs = review.corrections()
        assert len(pairs) == 1
        original, cor = pairs[0]
        assert original.source_field == "amount"
        assert cor.source_field == "total"

    def test_out_of_range_raises(self):
        review = ActionReview(_make_action_mappings())
        with pytest.raises(IndexError):
            review.approve(10)

    def test_proposed_is_copy(self):
        mappings = _make_action_mappings()
        review = ActionReview(mappings)
        assert review.proposed is not mappings
        assert review.proposed == mappings
