from __future__ import annotations

import ast
from typing import Any

_ALLOWED_BUILTINS = {"int", "float", "str", "bool", "abs", "round", "len", "min", "max"}

_builtins_ref = __builtins__ if isinstance(__builtins__, dict) else vars(__builtins__)
_SAFE_BUILTINS = {name: _builtins_ref[name] for name in _ALLOWED_BUILTINS}


class UnsafeExpressionError(Exception):
    pass


def evaluate(expression: str, value: Any) -> Any:
    """Evaluate a transform expression safely using AST whitelisting.

    Only allows: arithmetic, comparisons, attribute access on `value`,
    subscript access on `value`, and a whitelist of builtins.
    """
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        raise UnsafeExpressionError(f"Invalid expression syntax: {expression}") from e

    _validate_node(tree.body)

    code = compile(tree, "<transform>", "eval")
    try:
        return eval(code, {"__builtins__": _SAFE_BUILTINS}, {"value": value})
    except Exception as e:
        raise UnsafeExpressionError(f"Expression evaluation failed: {e}") from e


def _validate_node(node: ast.AST) -> None:
    match node:
        case ast.Expression(body=body):
            _validate_node(body)

        case ast.Constant():
            pass

        case ast.Name(id=name):
            if name not in {"value", *_ALLOWED_BUILTINS}:
                raise UnsafeExpressionError(f"Forbidden name: {name}")

        case ast.UnaryOp(operand=operand):
            _validate_node(operand)

        case ast.BinOp(left=left, right=right):
            _validate_node(left)
            _validate_node(right)

        case ast.BoolOp(values=values):
            for v in values:
                _validate_node(v)

        case ast.Compare(left=left, comparators=comparators):
            _validate_node(left)
            for c in comparators:
                _validate_node(c)

        case ast.IfExp(test=test, body=body, orelse=orelse):
            _validate_node(test)
            _validate_node(body)
            _validate_node(orelse)

        case ast.Attribute(value=attr_value):
            _validate_node(attr_value)

        case ast.Subscript(value=sub_value, slice=slice_):
            _validate_node(sub_value)
            _validate_node(slice_)

        case ast.Index(value=idx_value):
            _validate_node(idx_value)

        case ast.Call(func=func, args=args, keywords=keywords):
            _validate_node(func)
            for arg in args:
                _validate_node(arg)
            for kw in keywords:
                _validate_node(kw.value)

        case ast.List(elts=elts) | ast.Tuple(elts=elts) | ast.Set(elts=elts):
            for elt in elts:
                _validate_node(elt)

        case ast.Dict(keys=keys, values=values):
            for k in keys:
                if k is not None:
                    _validate_node(k)
            for v in values:
                _validate_node(v)

        case ast.Slice(lower=lower, upper=upper, step=step):
            for part in (lower, upper, step):
                if part is not None:
                    _validate_node(part)

        case _:
            raise UnsafeExpressionError(f"Forbidden AST node: {type(node).__name__}")
