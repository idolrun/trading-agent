"""No database transaction spans a model or broker call (database-schema.md §7, coding
practices §9): a structural scan of every `async with` that opens a session or transaction."""

import ast
from pathlib import Path

APP = Path(__file__).parents[2] / "app"
OPENERS = {"in_transaction", "transaction", "sessions", "_sessions", "session_factory", "factory"}
EXTERNAL_NAMES = {  # a dotted call name containing one of these is a broker / model / quote call
    "broker",
    "_broker",
    "_brokers",
    "md",
    "_md",
    "marketdata",
    "llm",
    "_llm",
    "jev",
    "_chain",
    "chain",
    "planner",
    "_planner",
    "submit_order",
    "quote",
    "tick_size",
    "ainvoke",
    "astream",
}


def _dotted(node: ast.expr) -> str:
    match node:
        case ast.Attribute():
            return f"{_dotted(node.value)}.{node.attr}"
        case ast.Name():
            return node.id
        case ast.Call():
            return _dotted(node.func)
    return "?"


def external_calls_in_transactions(source: str) -> list[tuple[int, str]]:
    hits = []
    for block in ast.walk(ast.parse(source)):
        if not isinstance(block, ast.AsyncWith):
            continue
        opens = any(
            isinstance(i.context_expr, ast.Call)
            and _dotted(i.context_expr.func).split(".")[-1] in OPENERS
            for i in block.items
        )
        if not opens:
            continue
        for node in ast.walk(block):
            if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
                name = _dotted(node.value.func)
                if EXTERNAL_NAMES & set(name.split(".")):
                    hits.append((node.lineno, name))
    return hits


def test_scan_finds_a_broker_call_inside_a_transaction() -> None:
    source = """
async def bad(self):
    async with in_transaction(self._sessions, None) as db:
        await self._broker.submit(request)
"""
    assert external_calls_in_transactions(source) == [(4, "self._broker.submit")]


def test_no_transaction_spans_a_model_or_broker_call() -> None:
    found = {
        f"{path.relative_to(APP)}:{line} {name}"
        for path in APP.rglob("*.py")
        for line, name in external_calls_in_transactions(path.read_text())
    }
    assert found == set()
