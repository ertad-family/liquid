from liquid.cache.key import compute_cache_key


class TestCacheKey:
    def test_same_inputs_same_key(self):
        k1 = compute_cache_key("adapter1", "/orders", {"limit": 10})
        k2 = compute_cache_key("adapter1", "/orders", {"limit": 10})
        assert k1 == k2

    def test_different_adapter_different_key(self):
        k1 = compute_cache_key("adapter1", "/orders", {})
        k2 = compute_cache_key("adapter2", "/orders", {})
        assert k1 != k2

    def test_param_order_irrelevant(self):
        k1 = compute_cache_key("a", "/p", {"b": 2, "a": 1})
        k2 = compute_cache_key("a", "/p", {"a": 1, "b": 2})
        assert k1 == k2

    def test_different_method_different_key(self):
        k1 = compute_cache_key("a", "/p", {}, method="GET")
        k2 = compute_cache_key("a", "/p", {}, method="POST")
        assert k1 != k2

    def test_different_path_different_key(self):
        k1 = compute_cache_key("a", "/orders", {})
        k2 = compute_cache_key("a", "/customers", {})
        assert k1 != k2

    def test_different_params_different_key(self):
        k1 = compute_cache_key("a", "/p", {"limit": 10})
        k2 = compute_cache_key("a", "/p", {"limit": 20})
        assert k1 != k2

    def test_nested_params_stable(self):
        k1 = compute_cache_key("a", "/p", {"filters": {"b": 1, "a": 2}})
        k2 = compute_cache_key("a", "/p", {"filters": {"a": 2, "b": 1}})
        assert k1 == k2

    def test_none_params_equiv_empty(self):
        k1 = compute_cache_key("a", "/p", None)
        k2 = compute_cache_key("a", "/p", {})
        assert k1 == k2

    def test_method_case_insensitive(self):
        k1 = compute_cache_key("a", "/p", {}, method="get")
        k2 = compute_cache_key("a", "/p", {}, method="GET")
        assert k1 == k2

    def test_returns_hex_string(self):
        k = compute_cache_key("a", "/p", {})
        assert isinstance(k, str)
        assert len(k) == 64  # sha256 hex
