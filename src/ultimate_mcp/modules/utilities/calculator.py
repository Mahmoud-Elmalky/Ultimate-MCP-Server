"""Strict AST-allowlisted mathematical calculator (replaces unsafe ``eval``).

Security guarantees (``docs/SECURITY_MODEL.md`` §6 & ``AGENT_PROMPT.md`` §7):
- Never calls ``eval`` or ``exec``.
- Only numeric literals (`int`, `float`), approved constants (`pi`, `e`, `tau`),
  arithmetic operators (`+`, `-`, `*`, `/`, `//`, `%`, `**`), unary `+`/`-`, and
  explicitly allowlisted `math` functions are permitted.
- Attribute access (`a.b`), subscripts (`a[0]`), comprehensions, lambdas, arbitrary names,
  and strings/bytes are unconditionally rejected.
- Enforces expression length, AST node count, recursion depth, exponent bounds, factorial
  argument bounds, and numeric magnitude bounds to prevent CPU/memory DoS (`9**9**9`).
"""

from __future__ import annotations

import ast
import math
from collections.abc import Callable
from typing import Any

from ultimate_mcp.core.models import ErrorCode, ToolResult, UltimateMCPError

MAX_EXPRESSION_LENGTH = 500
MAX_AST_NODES = 128
MAX_AST_DEPTH = 24
MAX_EXPONENT_ABS = 1_000
MAX_BASE_FOR_LARGE_EXP = 1_000_000.0
MAX_ABS_VALUE = 1e100
MAX_FACTORIAL_N = 170

ALLOWED_CONSTANTS: dict[str, float] = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
}


def _safe_factorial(x: float | int) -> int:
    if isinstance(x, bool) or not (isinstance(x, int) or (isinstance(x, float) and x.is_integer())):
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT,
            "factorial() requires a non-negative integer.",
        )
    n = int(x)
    if n < 0 or n > MAX_FACTORIAL_N:
        raise UltimateMCPError(
            ErrorCode.RESOURCE_LIMIT_EXCEEDED,
            f"factorial() argument must be between 0 and {MAX_FACTORIAL_N}.",
        )
    return math.factorial(n)


ALLOWED_FUNCTIONS: dict[str, tuple[Callable[..., Any], tuple[int, ...]]] = {
    "abs": (abs, (1,)),
    "round": (round, (1, 2)),
    "min": (min, (2, 3, 4, 5)),
    "max": (max, (2, 3, 4, 5)),
    "sqrt": (math.sqrt, (1,)),
    "cbrt": (math.cbrt, (1,)),
    "exp": (math.exp, (1,)),
    "log": (math.log, (1, 2)),
    "log10": (math.log10, (1,)),
    "log2": (math.log2, (1,)),
    "sin": (math.sin, (1,)),
    "cos": (math.cos, (1,)),
    "tan": (math.tan, (1,)),
    "asin": (math.asin, (1,)),
    "acos": (math.acos, (1,)),
    "atan": (math.atan, (1,)),
    "atan2": (math.atan2, (2,)),
    "sinh": (math.sinh, (1,)),
    "cosh": (math.cosh, (1,)),
    "tanh": (math.tanh, (1,)),
    "degrees": (math.degrees, (1,)),
    "radians": (math.radians, (1,)),
    "floor": (math.floor, (1,)),
    "ceil": (math.ceil, (1,)),
    "trunc": (math.trunc, (1,)),
    "hypot": (math.hypot, (2, 3)),
    "pow": (math.pow, (2,)),
    "factorial": (_safe_factorial, (1,)),
    "gcd": (math.gcd, (2, 3)),
    "lcm": (math.lcm, (2, 3)),
}


def _check_magnitude(value: int | float) -> int | float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT,
            f"Unsupported numeric result type: {type(value).__name__}.",
        )
    try:
        as_float = float(value)
    except OverflowError as exc:
        raise UltimateMCPError(
            ErrorCode.RESOURCE_LIMIT_EXCEEDED,
            "Numeric value overflowed floating-point range.",
        ) from exc
    if math.isnan(as_float) or math.isinf(as_float) or abs(as_float) > MAX_ABS_VALUE:
        raise UltimateMCPError(
            ErrorCode.RESOURCE_LIMIT_EXCEEDED,
            f"Numeric magnitude exceeds maximum allowed ({MAX_ABS_VALUE:g}).",
        )
    return value


def _safe_power(left: int | float, right: int | float) -> int | float:
    if abs(float(right)) > MAX_EXPONENT_ABS:
        raise UltimateMCPError(
            ErrorCode.RESOURCE_LIMIT_EXCEEDED,
            f"Exponent magnitude ({right}) exceeds maximum allowed ({MAX_EXPONENT_ABS}).",
        )
    if abs(float(left)) > MAX_BASE_FOR_LARGE_EXP and abs(float(right)) > 20:
        raise UltimateMCPError(
            ErrorCode.RESOURCE_LIMIT_EXCEEDED,
            "Base and exponent combination would exceed safe magnitude bounds.",
        )
    try:
        result = left**right
    except (OverflowError, ValueError, ZeroDivisionError) as exc:
        raise UltimateMCPError(ErrorCode.INVALID_INPUT, f"Power error: {exc}") from exc
    return _check_magnitude(result)


def evaluate_math_expression(expression: str) -> int | float:
    """Safely evaluate a mathematical expression via strict AST traversal."""
    cleaned = expression.strip()
    if not cleaned:
        raise UltimateMCPError(ErrorCode.INVALID_INPUT, "Mathematical expression is empty.")
    if len(cleaned) > MAX_EXPRESSION_LENGTH:
        raise UltimateMCPError(
            ErrorCode.RESOURCE_LIMIT_EXCEEDED,
            f"Expression length ({len(cleaned)}) exceeds limit ({MAX_EXPRESSION_LENGTH}).",
        )

    try:
        tree = ast.parse(cleaned, mode="eval")
    except SyntaxError as exc:
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT,
            f"Invalid mathematical syntax: {exc.msg}.",
        ) from exc

    node_count = sum(1 for _ in ast.walk(tree))
    if node_count > MAX_AST_NODES:
        raise UltimateMCPError(
            ErrorCode.RESOURCE_LIMIT_EXCEEDED,
            f"Expression complexity ({node_count} AST nodes) exceeds limit ({MAX_AST_NODES}).",
        )

    def _eval(node: ast.AST, depth: int) -> int | float:
        if depth > MAX_AST_DEPTH:
            raise UltimateMCPError(
                ErrorCode.RESOURCE_LIMIT_EXCEEDED,
                f"Expression nesting depth exceeds limit ({MAX_AST_DEPTH}).",
            )
        if isinstance(node, ast.Expression):
            return _eval(node.body, depth + 1)

        if isinstance(node, ast.Constant):
            val = node.value
            if isinstance(val, bool) or not isinstance(val, int | float):
                raise UltimateMCPError(
                    ErrorCode.INVALID_INPUT,
                    f"Only int and float literals are allowed (got {type(val).__name__}).",
                )
            return _check_magnitude(val)

        if isinstance(node, ast.Name):
            if node.id in ALLOWED_CONSTANTS:
                return ALLOWED_CONSTANTS[node.id]
            raise UltimateMCPError(
                ErrorCode.INVALID_INPUT,
                f"Unknown or disallowed identifier '{node.id}'.",
            )

        if isinstance(node, ast.UnaryOp):
            operand = _eval(node.operand, depth + 1)
            if isinstance(node.op, ast.UAdd):
                return _check_magnitude(+operand)
            if isinstance(node.op, ast.USub):
                return _check_magnitude(-operand)
            raise UltimateMCPError(
                ErrorCode.INVALID_INPUT,
                f"Unsupported unary operator '{type(node.op).__name__}'.",
            )

        if isinstance(node, ast.BinOp):
            left = _eval(node.left, depth + 1)
            right = _eval(node.right, depth + 1)
            op = node.op
            try:
                if isinstance(op, ast.Add):
                    return _check_magnitude(left + right)
                if isinstance(op, ast.Sub):
                    return _check_magnitude(left - right)
                if isinstance(op, ast.Mult):
                    return _check_magnitude(left * right)
                if isinstance(op, ast.Div):
                    if right == 0:
                        raise UltimateMCPError(ErrorCode.INVALID_INPUT, "Division by zero.")
                    return _check_magnitude(left / right)
                if isinstance(op, ast.FloorDiv):
                    if right == 0:
                        raise UltimateMCPError(ErrorCode.INVALID_INPUT, "Division by zero.")
                    return _check_magnitude(left // right)
                if isinstance(op, ast.Mod):
                    if right == 0:
                        raise UltimateMCPError(ErrorCode.INVALID_INPUT, "Modulo by zero.")
                    return _check_magnitude(left % right)
                if isinstance(op, ast.Pow):
                    return _safe_power(left, right)
            except OverflowError as exc:
                raise UltimateMCPError(
                    ErrorCode.RESOURCE_LIMIT_EXCEEDED,
                    "Arithmetic overflow.",
                ) from exc
            raise UltimateMCPError(
                ErrorCode.INVALID_INPUT,
                f"Unsupported binary operator '{type(op).__name__}'.",
            )

        if isinstance(node, ast.Call):
            if node.keywords:
                raise UltimateMCPError(
                    ErrorCode.INVALID_INPUT,
                    "Keyword arguments are not supported in calculator functions.",
                )
            if not isinstance(node.func, ast.Name):
                raise UltimateMCPError(
                    ErrorCode.INVALID_INPUT,
                    "Only direct calls to allowlisted math functions are permitted.",
                )
            fn_name = node.func.id
            entry = ALLOWED_FUNCTIONS.get(fn_name)
            if entry is None:
                raise UltimateMCPError(
                    ErrorCode.INVALID_INPUT,
                    f"Function '{fn_name}' is not in the calculator allowlist.",
                )
            fn, arities = entry
            if len(node.args) not in arities:
                raise UltimateMCPError(
                    ErrorCode.INVALID_INPUT,
                    f"Function '{fn_name}' expects {arities} argument(s), got {len(node.args)}.",
                )
            evaluated_args = [_eval(arg, depth + 1) for arg in node.args]
            if fn_name == "pow":
                return _safe_power(evaluated_args[0], evaluated_args[1])
            try:
                res = fn(*evaluated_args)
            except UltimateMCPError:
                raise
            except (ValueError, ZeroDivisionError, OverflowError, TypeError) as exc:
                raise UltimateMCPError(
                    ErrorCode.INVALID_INPUT,
                    f"Math error in {fn_name}(): {exc}",
                ) from exc
            return _check_magnitude(res)

        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT,
            f"Disallowed syntax construct '{type(node).__name__}' in calculator expression.",
        )

    return _eval(tree, 0)


def calculate_service(expression: str) -> ToolResult:
    """Service wrapper returning a standardized ``ToolResult`` for ``calculate``."""
    value = evaluate_math_expression(expression)
    return ToolResult.ok(
        tool="calculate",
        summary=f"Result: {value}",
        data={"expression": expression.strip(), "result": value},
    )
