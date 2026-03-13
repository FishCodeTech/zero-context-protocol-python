import asyncio
from datetime import timedelta

from zcp import CallRequest, CanonicalValidator, HandleStore, RuntimeExecutor, SessionState, ToolDefinition, ToolRegistry
from zcp.canonical_runtime import expand_handle


def build_runtime() -> tuple[RuntimeExecutor, SessionState]:
    session = SessionState(session_id="s1")
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            tool_id="17",
            alias="web.search",
            description_short="Search documents.",
            input_schema={
                "type": "object",
                "properties": {
                    "q": {"type": "string"},
                    "k": {"type": "integer"},
                },
                "required": ["q"],
                "additionalProperties": False,
            },
            handler=lambda arguments: [
                {"title": arguments["q"], "rank": idx}
                for idx in range(arguments.get("k", 2))
            ],
            handle_kind="search_results",
        )
    )
    registry.register(
        ToolDefinition(
            tool_id="18",
            alias="math.add",
            description_short="Add two integers.",
            input_schema={
                "type": "object",
                "properties": {
                    "a": {"type": "integer"},
                    "b": {"type": "integer"},
                },
                "required": ["a", "b"],
                "additionalProperties": False,
            },
            output_mode="scalar",
            inline_ok=True,
            handler=lambda arguments: arguments["a"] + arguments["b"],
            handle_kind="number",
        )
    )
    store = HandleStore(session, default_ttl=timedelta(minutes=5))
    executor = RuntimeExecutor(registry, CanonicalValidator(), store)
    return executor, session


def test_runtime_executor_returns_handle_for_nontrivial_results() -> None:
    executor, session = build_runtime()

    result = asyncio.run(
        executor.execute_call(
            CallRequest(cid="c1", tool_id="17", alias="web.search", arguments={"q": "foo", "k": "2"})
        )
    )

    assert result.status == "ok"
    assert result.handle is not None
    assert result.handle.id.startswith("#S")
    assert expand_handle(session, result.handle.id, "count") == 2
    assert expand_handle(session, result.handle.id, "read", item=1, fields=["title"]) == {"title": "foo"}


def test_runtime_executor_inlines_scalar_results() -> None:
    executor, _session = build_runtime()

    result = asyncio.run(
        executor.execute_call(
            CallRequest(cid="c2", tool_id="18", alias="math.add", arguments={"a": 2, "b": "3"})
        )
    )

    assert result.status == "ok"
    assert result.scalar == 5
    assert result.handle is None


def test_runtime_executor_returns_compact_validation_error() -> None:
    executor, _session = build_runtime()

    result = asyncio.run(
        executor.execute_call(
            CallRequest(cid="c3", tool_id="18", alias="math.add", arguments={"a": 2})
        )
    )

    assert result.status == "error"
    assert result.error.code == "missing:b"
