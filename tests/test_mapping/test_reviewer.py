import pytest

from liquid.mapping.reviewer import MappingReview, MappingStatus
from liquid.models.adapter import FieldMapping


def _make_mappings() -> list[FieldMapping]:
    return [
        FieldMapping(source_path="total_price", target_field="amount", confidence=0.8),
        FieldMapping(source_path="created_at", target_field="date", confidence=0.9),
        FieldMapping(source_path="customer.email", target_field="email", confidence=0.7),
    ]


class TestMappingReview:
    def test_initial_state(self):
        review = MappingReview(_make_mappings())
        assert len(review) == 3
        assert review.status(0) == MappingStatus.PENDING
        assert review.status(1) == MappingStatus.PENDING

    def test_approve(self):
        review = MappingReview(_make_mappings())
        review.approve(0)
        assert review.status(0) == MappingStatus.APPROVED

    def test_reject(self):
        review = MappingReview(_make_mappings())
        review.reject(1)
        assert review.status(1) == MappingStatus.REJECTED

    def test_correct(self):
        review = MappingReview(_make_mappings())
        review.correct(0, target_field="revenue")
        assert review.status(0) == MappingStatus.CORRECTED

    def test_approve_all(self):
        review = MappingReview(_make_mappings())
        review.reject(1)
        review.approve_all()
        assert review.status(0) == MappingStatus.APPROVED
        assert review.status(1) == MappingStatus.REJECTED  # not overridden
        assert review.status(2) == MappingStatus.APPROVED

    def test_finalize_approved_only(self):
        review = MappingReview(_make_mappings())
        review.approve(0)
        review.reject(1)
        review.approve(2)
        result = review.finalize()
        assert len(result) == 2
        assert result[0].target_field == "amount"
        assert result[1].target_field == "email"

    def test_finalize_includes_corrections(self):
        review = MappingReview(_make_mappings())
        review.approve(0)
        review.correct(1, target_field="timestamp")
        review.reject(2)
        result = review.finalize()
        assert len(result) == 2
        assert result[1].target_field == "timestamp"
        assert result[1].confidence == 1.0

    def test_corrections_returns_pairs(self):
        review = MappingReview(_make_mappings())
        review.correct(0, target_field="revenue")
        pairs = review.corrections()
        assert len(pairs) == 1
        original, corrected = pairs[0]
        assert original.target_field == "amount"
        assert corrected.target_field == "revenue"

    def test_out_of_range_raises(self):
        review = MappingReview(_make_mappings())
        with pytest.raises(IndexError):
            review.approve(10)

    def test_proposed_is_copy(self):
        mappings = _make_mappings()
        review = MappingReview(mappings)
        assert review.proposed is not mappings
        assert review.proposed == mappings
