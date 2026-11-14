"""Bounded interpreter for a small numeric AST subset; never executes candidate bytecode."""

import ast
import json
import math
import operator
import sys

MAX_SOURCE = 16000
MAX_INPUT = 65536
MAX_NODES = 1000
MAX_STEPS = 50000
MAX_ITEMS = 1000
MAX_DEPTH = 24
MAX_BITS = 256


class HarnessError(ValueError):
    pass


def _safe(value, depth=0, counts=None):
    if counts is None:
        counts = [0]
    counts[0] += 1
    if depth > MAX_DEPTH or counts[0] > MAX_ITEMS:
        raise HarnessError("Value exceeds limits")
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if value.bit_length() > MAX_BITS:
            raise HarnessError("Integer exceeds limit")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise HarnessError("Non-finite value")
        return value
    if type(value) is str:
        if len(value) > 1000:
            raise HarnessError("String exceeds limit")
        return value
    if type(value) is list:
        return [_safe(item, depth + 1, counts) for item in value]
    if type(value) is dict:
        if any(type(key) is not str or len(key) > 100 or key.startswith("_") for key in value):
            raise HarnessError("Invalid dictionary key")
        return {key: _safe(item, depth + 1, counts) for key, item in value.items()}
    raise HarnessError("Unsupported value")


class _Return(Exception):
    def __init__(self, value):
        self.value = value


class Interpreter:
    def __init__(self, inputs):
        if type(inputs) is not dict:
            raise HarnessError("Input must be an object")
        self.env = _safe(inputs)
        if any(
            not key.isidentifier()
            or key.startswith("_")
            or key in {"range", "len", "sum", "min", "max", "abs"}
            for key in self.env
        ):
            raise HarnessError("Invalid input name")
        self.steps = 0

    def tick(self, depth):
        self.steps += 1
        if self.steps > MAX_STEPS or depth > MAX_DEPTH:
            raise HarnessError("Execution limit exceeded")

    def expr(self, node, depth=0):
        self.tick(depth)
        if isinstance(node, ast.Constant):
            return _safe(node.value)
        if isinstance(node, ast.Name):
            if node.id.startswith("_") or node.id not in self.env:
                raise HarnessError("Unknown variable")
            return self.env[node.id]
        if isinstance(node, ast.List):
            return _safe([self.expr(item, depth + 1) for item in node.elts])
        if isinstance(node, ast.Dict):
            keys = [self.expr(item, depth + 1) for item in node.keys]
            if any(type(key) is not str for key in keys) or len(keys) != len(set(keys)):
                raise HarnessError("Invalid dictionary")
            return _safe({key: self.expr(item, depth + 1) for key, item in zip(keys, node.values)})
        if isinstance(node, ast.Subscript):
            container = self.expr(node.value, depth + 1)
            key = self.expr(node.slice, depth + 1)
            if (
                type(container) not in {list, dict}
                or (type(container) is list and type(key) is not int)
                or (type(container) is dict and type(key) is not str)
            ):
                raise HarnessError("Invalid index")
            try:
                return container[key]
            except (KeyError, IndexError) as exc:
                raise HarnessError("Index unavailable") from exc
        if isinstance(node, ast.UnaryOp):
            value = self.expr(node.operand, depth + 1)
            if isinstance(node.op, ast.Not):
                return not bool(value)
            if type(value) not in {int, float}:
                raise HarnessError("Numeric operand required")
            if isinstance(node.op, ast.USub):
                return _safe(-value)
            if isinstance(node.op, ast.UAdd):
                return _safe(value)
        if isinstance(node, ast.BinOp):
            left, right = self.expr(node.left, depth + 1), self.expr(node.right, depth + 1)
            if type(left) not in {int, float} or type(right) not in {int, float}:
                raise HarnessError("Numeric operands required")
            operations = {
                ast.Add: operator.add,
                ast.Sub: operator.sub,
                ast.Mult: operator.mul,
                ast.Div: operator.truediv,
                ast.FloorDiv: operator.floordiv,
                ast.Mod: operator.mod,
            }
            if isinstance(node.op, ast.Pow):
                if (
                    type(right) is not int
                    or abs(right) > 16
                    or (type(left) is int and left.bit_length() * max(right, 0) > MAX_BITS)
                ):
                    raise HarnessError("Power exceeds limit")
                function = operator.pow
            else:
                function = operations.get(type(node.op))
            if function is None:
                raise HarnessError("Unsupported operator")
            try:
                return _safe(function(left, right))
            except (ArithmeticError, OverflowError) as exc:
                raise HarnessError("Arithmetic failure") from exc
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left, right = self.expr(node.left, depth + 1), self.expr(node.comparators[0], depth + 1)
            operations = {
                ast.Eq: operator.eq,
                ast.NotEq: operator.ne,
                ast.Lt: operator.lt,
                ast.LtE: operator.le,
                ast.Gt: operator.gt,
                ast.GtE: operator.ge,
            }
            function = operations.get(type(node.ops[0]))
            if (
                function is None
                or type(left) not in {int, float, bool}
                or type(right) not in {int, float, bool}
            ):
                raise HarnessError("Invalid comparison")
            return function(left, right)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            name = node.func.id
            if name not in {"range", "len", "sum", "min", "max", "abs"}:
                raise HarnessError("Call is not allowed")
            args = [self.expr(arg, depth + 1) for arg in node.args]
            if name == "range":
                if not 1 <= len(args) <= 3 or any(
                    type(arg) is not int or abs(arg) > MAX_ITEMS for arg in args
                ):
                    raise HarnessError("Invalid range")
                try:
                    result = list(range(*args))
                except ValueError as exc:
                    raise HarnessError("Invalid range") from exc
                return _safe(result)
            if len(args) != 1:
                raise HarnessError("Invalid call")
            arg = args[0]
            if name == "abs" and type(arg) in {int, float}:
                return _safe(abs(arg))
            if name == "len" and type(arg) in {list, dict}:
                return len(arg)
            if (
                name in {"sum", "min", "max"}
                and type(arg) is list
                and arg
                and all(type(item) in {int, float} for item in arg)
            ):
                return _safe({"sum": sum, "min": min, "max": max}[name](arg))
            raise HarnessError("Invalid call input")
        raise HarnessError("Expression is not allowed")

    def block(self, statements, depth=0):
        for node in statements:
            self.tick(depth)
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                name = node.targets[0].id
                if name.startswith("_") or name in {"range", "len", "sum", "min", "max", "abs"}:
                    raise HarnessError("Invalid assignment")
                self.env[name] = _safe(self.expr(node.value, depth + 1))
                _safe(self.env)
            elif isinstance(node, ast.If):
                self.block(node.body if self.expr(node.test, depth + 1) else node.orelse, depth + 1)
            elif (
                isinstance(node, ast.For) and isinstance(node.target, ast.Name) and not node.orelse
            ):
                name = node.target.id
                if name.startswith("_") or name in {"range", "len", "sum", "min", "max", "abs"}:
                    raise HarnessError("Invalid loop variable")
                values = self.expr(node.iter, depth + 1)
                if type(values) is not list or len(values) > MAX_ITEMS:
                    raise HarnessError("Invalid loop input")
                for value in values:
                    self.tick(depth + 1)
                    self.env[name] = value
                    self.block(node.body, depth + 1)
            elif isinstance(node, ast.Return) and node.value is not None:
                raise _Return(_safe(self.expr(node.value, depth + 1)))
            else:
                raise HarnessError("Statement is not allowed")


def interpret(source, inputs):
    if not isinstance(source, str) or len(source.encode("utf-8")) > MAX_SOURCE:
        raise HarnessError("Source exceeds limit")
    try:
        tree = ast.parse(source, mode="exec")
        nodes = list(ast.walk(tree))
        if len(nodes) > MAX_NODES:
            raise HarnessError("AST exceeds limit")
        allowed = {
            ast.Module,
            ast.Assign,
            ast.Name,
            ast.Store,
            ast.Load,
            ast.Constant,
            ast.List,
            ast.Dict,
            ast.Subscript,
            ast.UnaryOp,
            ast.Not,
            ast.USub,
            ast.UAdd,
            ast.BinOp,
            ast.Add,
            ast.Sub,
            ast.Mult,
            ast.Div,
            ast.FloorDiv,
            ast.Mod,
            ast.Pow,
            ast.Compare,
            ast.Eq,
            ast.NotEq,
            ast.Lt,
            ast.LtE,
            ast.Gt,
            ast.GtE,
            ast.Call,
            ast.If,
            ast.For,
            ast.Return,
        }
        if any(
            type(node) not in allowed or (isinstance(node, ast.Name) and node.id.startswith("_"))
            for node in nodes
        ):
            raise HarnessError("AST construct is not allowed")
        interpreter = Interpreter(inputs)
        try:
            interpreter.block(tree.body)
        except _Return as result:
            value = result.value
        else:
            if "result" not in interpreter.env:
                raise HarnessError("A result is required")
            value = interpreter.env["result"]
        return {
            "result": _safe(value),
            "steps": interpreter.steps,
            "subset": "forge_numeric_ast_v1",
        }
    except (SyntaxError, RecursionError, MemoryError, TypeError, OverflowError) as exc:
        raise HarnessError("Invalid or excessive input") from exc


if __name__ == "__main__":
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise HarnessError("Input exceeds limit")
        payload = json.loads(
            raw, parse_constant=lambda _: (_ for _ in ()).throw(HarnessError("Invalid number"))
        )
        if type(payload) is not dict or set(payload) != {"source", "inputs"}:
            raise HarnessError("Invalid request")
        result = interpret(payload["source"], payload["inputs"])
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_INPUT:
            raise HarnessError("Output exceeds limit")
        sys.stdout.write(encoded)
    except (HarnessError, ValueError, TypeError):
        sys.stderr.write("numeric interpreter rejected input")
        sys.exit(1)
