import pytest

from forge_core.numeric_harness import HarnessError, interpret


def test_numeric_algorithm_uses_bounded_interpreter():
    result = interpret(
        "total = 0\nfor x in data:\n    total = total + x\nresult = total", {"data": [1, 2, 3]}
    )
    assert result["result"] == 6
    assert result["steps"] > 0


@pytest.mark.parametrize(
    "source",
    [
        "import os",
        "result = (1).__class__",
        "result = __import__('os')",
        "result = 2 ** 100000",
        "while True:\n    pass",
        "result = [0] * 10000000",
    ],
)
def test_interpreter_rejects_escape_and_resource_exhaustion(source):
    with pytest.raises(HarnessError):
        interpret(source, {})
