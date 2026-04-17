import pytest

from liquid.query.dsl import QueryError, validate_query


class TestValidation:
    def test_valid_simple(self):
        validate_query({"status": "paid"})

    def test_valid_with_ops(self):
        validate_query({"total": {"$gt": 100, "$lte": 1000}})

    def test_valid_logical(self):
        validate_query({"$or": [{"status": "paid"}, {"status": "pending"}]})

    def test_invalid_not_dict(self):
        with pytest.raises(QueryError):
            validate_query("invalid")

    def test_unknown_operator(self):
        with pytest.raises(QueryError):
            validate_query({"status": {"$bogus": "x"}})

    def test_in_requires_list(self):
        with pytest.raises(QueryError):
            validate_query({"status": {"$in": "paid"}})
