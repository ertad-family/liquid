from liquid.query.engine import apply_query


def _orders():
    return [
        {"id": 1, "status": "paid", "total": 100, "email": "a@example.com"},
        {"id": 2, "status": "pending", "total": 50, "email": "b@gmail.com"},
        {"id": 3, "status": "paid", "total": 200, "email": "c@example.com"},
        {"id": 4, "status": "refunded", "total": 150},  # no email
    ]


class TestComparison:
    def test_eq_implicit(self):
        result = apply_query(_orders(), {"status": "paid"})
        assert len(result) == 2

    def test_eq_explicit(self):
        result = apply_query(_orders(), {"status": {"$eq": "paid"}})
        assert len(result) == 2

    def test_gt(self):
        result = apply_query(_orders(), {"total": {"$gt": 100}})
        assert len(result) == 2
        assert all(r["total"] > 100 for r in result)

    def test_gte(self):
        result = apply_query(_orders(), {"total": {"$gte": 100}})
        assert len(result) == 3

    def test_combined_bounds(self):
        result = apply_query(_orders(), {"total": {"$gte": 100, "$lte": 150}})
        ids = [r["id"] for r in result]
        assert ids == [1, 4]


class TestCollections:
    def test_in(self):
        result = apply_query(_orders(), {"status": {"$in": ["paid", "pending"]}})
        assert len(result) == 3

    def test_nin(self):
        result = apply_query(_orders(), {"status": {"$nin": ["paid"]}})
        assert len(result) == 2


class TestStrings:
    def test_contains(self):
        result = apply_query(_orders(), {"email": {"$contains": "@gmail"}})
        assert len(result) == 1
        assert result[0]["id"] == 2

    def test_icontains(self):
        result = apply_query(_orders(), {"email": {"$icontains": "@GMAIL"}})
        assert len(result) == 1


class TestLogical:
    def test_or(self):
        result = apply_query(
            _orders(),
            {"$or": [{"status": "refunded"}, {"total": {"$gt": 150}}]},
        )
        ids = sorted(r["id"] for r in result)
        assert ids == [3, 4]

    def test_and_implicit(self):
        result = apply_query(_orders(), {"status": "paid", "total": {"$gt": 150}})
        assert len(result) == 1
        assert result[0]["id"] == 3

    def test_not(self):
        result = apply_query(_orders(), {"$not": {"status": "paid"}})
        ids = sorted(r["id"] for r in result)
        assert ids == [2, 4]


class TestExistence:
    def test_exists_true(self):
        result = apply_query(_orders(), {"email": {"$exists": True}})
        assert len(result) == 3

    def test_exists_false(self):
        result = apply_query(_orders(), {"email": {"$exists": False}})
        assert len(result) == 1
