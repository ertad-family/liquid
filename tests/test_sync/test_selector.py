from liquid.sync.selector import RecordSelector


class TestRecordSelector:
    def test_none_path_with_list(self):
        sel = RecordSelector()
        assert sel.select([{"a": 1}, {"a": 2}]) == [{"a": 1}, {"a": 2}]

    def test_none_path_with_dict(self):
        sel = RecordSelector()
        assert sel.select({"a": 1}) == [{"a": 1}]

    def test_none_path_with_scalar(self):
        sel = RecordSelector()
        assert sel.select("not a dict") == []

    def test_simple_path(self):
        sel = RecordSelector("data")
        assert sel.select({"data": [{"id": 1}]}) == [{"id": 1}]

    def test_nested_path(self):
        sel = RecordSelector("response.data.items")
        data = {"response": {"data": {"items": [{"id": 1}, {"id": 2}]}}}
        assert sel.select(data) == [{"id": 1}, {"id": 2}]

    def test_path_to_dict(self):
        sel = RecordSelector("user")
        assert sel.select({"user": {"name": "Alice"}}) == [{"name": "Alice"}]

    def test_missing_path(self):
        sel = RecordSelector("nonexistent")
        assert sel.select({"data": []}) == []

    def test_broken_path(self):
        sel = RecordSelector("a.b.c")
        assert sel.select({"a": {"x": 1}}) == []
