import pytest

from liquid.sync.transform import UnsafeExpressionError, evaluate


class TestEvaluate:
    def test_arithmetic(self):
        assert evaluate("value * -1", 100) == -100
        assert evaluate("value + 10", 5) == 15
        assert evaluate("value / 2", 10) == 5.0

    def test_string_methods(self):
        assert evaluate("value.lower()", "HELLO") == "hello"
        assert evaluate("value.upper()", "hello") == "HELLO"
        assert evaluate("value.strip()", "  hi  ") == "hi"

    def test_builtins(self):
        assert evaluate("int(value)", "42") == 42
        assert evaluate("float(value)", "3.14") == 3.14
        assert evaluate("str(value)", 42) == "42"
        assert evaluate("abs(value)", -5) == 5
        assert evaluate("round(value, 2)", 3.14159) == 3.14
        assert evaluate("len(value)", [1, 2, 3]) == 3

    def test_conditional(self):
        assert evaluate("value if value > 0 else 0", 5) == 5
        assert evaluate("value if value > 0 else 0", -5) == 0

    def test_constant(self):
        assert evaluate("42", None) == 42
        assert evaluate("'fixed'", None) == "fixed"


class TestUnsafeExpressions:
    def test_import_blocked(self):
        with pytest.raises(UnsafeExpressionError):
            evaluate("__import__('os')", None)

    def test_unknown_name_blocked(self):
        with pytest.raises(UnsafeExpressionError):
            evaluate("os.system('ls')", None)

    def test_lambda_blocked(self):
        with pytest.raises(UnsafeExpressionError):
            evaluate("(lambda: value)()", 1)

    def test_comprehension_blocked(self):
        with pytest.raises(UnsafeExpressionError):
            evaluate("[x for x in value]", [1, 2])

    def test_syntax_error(self):
        with pytest.raises(UnsafeExpressionError):
            evaluate("def f(): pass", None)
