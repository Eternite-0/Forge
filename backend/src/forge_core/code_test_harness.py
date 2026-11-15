"""Pure Python function tests interpreted as bounded AST, never exec/eval.

Candidate source cannot access Python objects, imports, files or processes. Tests
exercise its actual functions against supplied JSON cases; they are not syntax-only
checks. This deliberately supports algorithm code, not an arbitrary test runner.
"""

import ast
import importlib.util
import json
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "_forge_numeric_test_base", Path(__file__).with_name("numeric_harness.py")
)
_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_base)
HarnessError = _base.HarnessError


class FunctionInterpreter(_base.Interpreter):
    def __init__(self, inputs, functions, meter, frames=0):
        super().__init__(inputs)
        self.functions = functions
        self.meter = meter
        self.frames = frames

    def tick(self, depth):
        self.meter[0] += 1
        if self.meter[0] > _base.MAX_STEPS or depth + self.frames > _base.MAX_DEPTH:
            raise HarnessError("Execution limit exceeded")

    def call(self, name, values):
        node = self.functions.get(name)
        if node is None or len(values) != len(node.args.args):
            raise HarnessError("Unknown function or invalid argument count")
        if self.frames >= _base.MAX_DEPTH:
            raise HarnessError("Call depth exceeded")
        local = dict(zip((arg.arg for arg in node.args.args), values))
        child = FunctionInterpreter(local, self.functions, self.meter, self.frames + 1)
        try:
            child.block(node.body)
        except _base._Return as result:
            return _base._safe(result.value)
        raise HarnessError("A function return is required")

    def expr(self, node, depth=0):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in self.functions
            and not node.keywords
        ):
            self.tick(depth)
            return self.call(node.func.id, [self.expr(arg, depth + 1) for arg in node.args])
        if isinstance(node, ast.BoolOp):
            self.tick(depth)
            value = self.expr(node.values[0], depth + 1)
            for part in node.values[1:]:
                if isinstance(node.op, ast.And) and not value:
                    return value
                if isinstance(node.op, ast.Or) and value:
                    return value
                value = self.expr(part, depth + 1)
            return value
        if isinstance(node, ast.IfExp):
            self.tick(depth)
            return self.expr(
                node.body if self.expr(node.test, depth + 1) else node.orelse, depth + 1
            )
        return super().expr(node, depth)

    def block(self, statements, depth=0):
        for node in statements:
            if isinstance(node, ast.While) and not node.orelse:
                self.tick(depth)
                while self.expr(node.test, depth + 1):
                    self.tick(depth)
                    self.block(node.body, depth + 1)
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                if not isinstance(node.value.value, str):
                    raise HarnessError("Only docstrings are permitted as expressions")
            else:
                super().block([node], depth)


def _functions(source):
    if not isinstance(source, str) or len(source.encode("utf-8")) > _base.MAX_SOURCE:
        raise HarnessError("Source exceeds limit")
    tree = ast.parse(source)
    nodes = list(ast.walk(tree))
    if len(nodes) > _base.MAX_NODES:
        raise HarnessError("AST exceeds limit")
    # The base numeric subset stays unchanged. New constructs apply only to this
    # explicit function-test contract and are still interpreted, never compiled.
    allowed = {
        ast.Module,
        ast.FunctionDef,
        ast.arguments,
        ast.arg,
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
        ast.BoolOp,
        ast.And,
        ast.Or,
        ast.IfExp,
        ast.While,
        ast.Expr,
    }
    if any(type(node) not in allowed for node in nodes):
        raise HarnessError("AST construct is not allowed")
    if any(
        isinstance(node, (ast.Name, ast.arg))
        and (getattr(node, "id", getattr(node, "arg", "")).startswith("_"))
        for node in nodes
    ):
        raise HarnessError("Private names are not permitted")
    functions = {}
    reserved = {"range", "len", "sum", "min", "max", "abs"}
    for node in tree.body:
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            continue
        if (
            not isinstance(node, ast.FunctionDef)
            or node.name.startswith("_")
            or node.name in functions
            or node.name in reserved
            or node.decorator_list
            or node.returns is not None
            or node.type_params
            or node.args.posonlyargs
            or node.args.kwonlyargs
            or node.args.vararg
            or node.args.kwarg
            or node.args.defaults
            or any(arg.annotation is not None or arg.arg in reserved for arg in node.args.args)
            or len({arg.arg for arg in node.args.args}) != len(node.args.args)
        ):
            raise HarnessError("Only plain, uniquely named pure functions are supported")
        # Nested function definitions would otherwise acquire misleading scope.
        if any(
            isinstance(child, ast.FunctionDef) for part in node.body for child in ast.walk(part)
        ):
            raise HarnessError("Nested functions are not supported")
        functions[node.name] = node
    if not 1 <= len(functions) <= 16:
        raise HarnessError("Invalid function count")
    return functions


def run_tests(source, function, suite):
    try:
        functions = _functions(source)
        if not isinstance(function, str) or function not in functions:
            raise HarnessError("Test function is unavailable")
        if type(suite) is not dict or set(suite) != {"cases"}:
            raise HarnessError("Expected an explicit case suite")
        cases = suite["cases"]
        if type(cases) is not list or not 1 <= len(cases) <= 32:
            raise HarnessError("Case count exceeds limit")
        parameters = [arg.arg for arg in functions[function].args.args]
        results = []
        meter = [0]
        for index, case in enumerate(cases):
            if type(case) is not dict or set(case) != {"inputs", "expected"}:
                raise HarnessError("Invalid test case")
            inputs, expected = _base._safe(case["inputs"]), _base._safe(case["expected"])
            if type(inputs) is not dict or set(inputs) != set(parameters):
                raise HarnessError("Test inputs must bind every function parameter")
            try:
                actual = FunctionInterpreter({}, functions, meter).call(
                    function, [inputs[name] for name in parameters]
                )
                # JSON values have distinct bool/int types even though Python
                # considers True == 1. Recursively compare serialized results.
                equal = json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True)
                results.append(
                    {"case": index, "passed": equal, "actual": actual, "expected": expected}
                )
            except (HarnessError, ArithmeticError, IndexError, KeyError) as exc:
                results.append({"case": index, "passed": False, "error": type(exc).__name__})
        passed = sum(item["passed"] for item in results)
        return {
            "subset": "forge_pure_function_tests_v1",
            "passed": passed,
            "failed": len(cases) - passed,
            "steps": meter[0],
            "results": results,
        }
    except (SyntaxError, RecursionError, MemoryError, TypeError, OverflowError, ValueError) as exc:
        raise HarnessError("Invalid or excessive function test input") from exc


if __name__ == "__main__":
    try:
        raw = sys.stdin.buffer.read(_base.MAX_INPUT + 1)
        if len(raw) > _base.MAX_INPUT:
            raise HarnessError("Input exceeds limit")
        payload = json.loads(raw)
        if type(payload) is not dict or set(payload) != {"source", "function", "suite"}:
            raise HarnessError("Invalid request")
        result = run_tests(payload["source"], payload["function"], payload["suite"])
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > _base.MAX_INPUT:
            raise HarnessError("Output exceeds limit")
        sys.stdout.write(encoded)
        if result["failed"]:
            sys.exit(2)
    except (HarnessError, ValueError, TypeError):
        sys.stderr.write("pure function test interpreter rejected input")
        sys.exit(1)
