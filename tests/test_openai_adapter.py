from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

from zcp import CanonicalValidator, HandleStore, SessionState, ToolDefinition, ToolRegistry, decode_tool_output
from zcp.adapters.openai import AgentLoop, OpenAIResponsesAdapter
from zcp.canonical_runtime import RuntimeExecutor


@dataclass
class FakeResponse:
    id: str
    output: list[dict]
    output_text: str | None = None


class FakeResponsesAPI:
    def __init__(
        self,
        responses: list[FakeResponse],
        stream_events: list[dict] | None = None,
        create_error: Exception | None = None,
    ) -> None:
        self._responses = list(responses)
        self._stream_events = stream_events or []
        self._create_error = create_error
        self.create_calls: list[dict] = []

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        if self._create_error is not None:
            raise self._create_error
        return self._responses.pop(0)

    def stream(self, **kwargs):
        self.create_calls.append(kwargs)
        return FakeStream(self._stream_events)


class FakeStream:
    def __init__(self, events: list[dict]) -> None:
        self._events = events

    def __enter__(self):
        return iter(self._events)

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeClient:
    def __init__(
        self,
        responses: list[FakeResponse],
        stream_events: list[dict] | None = None,
        response_error: Exception | None = None,
        chat_responses: list[Any] | None = None,
        chat_stream_events: list[Any] | None = None,
    ) -> None:
        self.responses = FakeResponsesAPI(responses, stream_events=stream_events, create_error=response_error)
        self.chat = SimpleNamespace(
            completions=FakeChatCompletionsAPI(chat_responses or [], stream_events=chat_stream_events or [])
        )


class FakeChatCompletionsAPI:
    def __init__(self, responses: list[Any], stream_events: list[Any]) -> None:
        self._responses = list(responses)
        self._stream_events = stream_events
        self.create_calls: list[dict] = []

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        if kwargs.get("stream"):
            return iter(self._stream_events)
        return self._responses.pop(0)


def make_chat_response(
    *,
    message_content: str | None = None,
    tool_calls: list[dict] | None = None,
    response_id: str = "chat_1",
):
    message = SimpleNamespace(
        role="assistant",
        content=message_content,
        tool_calls=tool_calls or [],
        model_dump=lambda exclude_none=True: {
            key: value
            for key, value in {
                "role": "assistant",
                "content": message_content,
                "tool_calls": tool_calls,
            }.items()
            if value is not None
        },
    )
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(id=response_id, choices=[choice])


def build_adapter() -> tuple[OpenAIResponsesAdapter, SessionState]:
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
                },
                "required": ["q"],
                "additionalProperties": False,
            },
            handler=lambda arguments: [{"title": arguments["q"], "url": "https://example.com"}],
            handle_kind="web_results",
        )
    )
    registry.register(
        ToolDefinition(
            tool_id="31",
            alias="mail.send",
            description_short="Send email.",
            input_schema={
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                },
                "required": ["to"],
                "additionalProperties": False,
            },
            handler=lambda arguments: arguments,
            flags=frozenset({"approval"}),
        )
    )
    executor = RuntimeExecutor(registry, CanonicalValidator(), HandleStore(session))
    return OpenAIResponsesAdapter(registry, executor), session


def test_run_turn_executes_function_calls_and_submits_outputs() -> None:
    adapter, session = build_adapter()
    client = FakeClient(
        responses=[
            FakeResponse(
                id="resp_1",
                output=[
                    {
                        "type": "function_call",
                        "name": "web_search",
                        "arguments": '{"q":"compact tools"}',
                        "call_id": "call_1",
                    }
                ],
            )
        ]
    )

    result = asyncio.run(
        adapter.run_turn(client, "gpt-4.1", [{"role": "user", "content": "find docs"}], session)
    )

    assert result.has_function_calls is True
    assert len(result.call_results) == 1
    payload = decode_tool_output(result.submitted_outputs[0]["output"])
    assert payload["ok"] is True
    assert payload["handle"].startswith("#W")
    assert client.responses.create_calls[0]["tools"][0]["name"] == "web_search"
    assert result.endpoint_used == "responses"


def test_agent_loop_continues_until_final_text() -> None:
    adapter, session = build_adapter()
    client = FakeClient(
        responses=[
            FakeResponse(
                id="resp_1",
                output=[
                    {
                        "type": "function_call",
                        "name": "web_search",
                        "arguments": '{"q":"compact tools"}',
                        "call_id": "call_1",
                    }
                ],
            ),
            FakeResponse(
                id="resp_2",
                output=[
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Here are the results."}],
                    }
                ],
                output_text="Here are the results.",
            ),
        ]
    )

    loop = AgentLoop(adapter)
    result = asyncio.run(loop.run(client, "gpt-4.1", [{"role": "user", "content": "find docs"}], session))

    assert result.final_output_text == "Here are the results."
    assert client.responses.create_calls[1]["previous_response_id"] == "resp_1"
    assert client.responses.create_calls[1]["input"][0]["type"] == "function_call_output"


def test_stream_turn_aggregates_argument_deltas_and_emits_results() -> None:
    adapter, session = build_adapter()
    client = FakeClient(
        responses=[],
        stream_events=[
            {
                "type": "response.output_item.added",
                "item": {"type": "function_call", "name": "web_search", "call_id": "call_1", "arguments": ""},
            },
            {"type": "response.function_call_arguments.delta", "call_id": "call_1", "delta": '{"q":"compact'},
            {"type": "response.function_call_arguments.done", "call_id": "call_1", "arguments": '{"q":"compact tools"}'},
        ],
    )

    async def collect():
        return [event async for event in adapter.stream_turn(client, "gpt-4.1", [], session)]

    events = asyncio.run(collect())

    assert events[0].kind == "call"
    assert events[0].payload["arguments"] == {"q": "compact tools"}
    assert events[1].kind == "result"
    assert events[-1].kind == "done"


def test_approval_tool_returns_error_envelope() -> None:
    adapter, session = build_adapter()
    client = FakeClient(
        responses=[
            FakeResponse(
                id="resp_1",
                output=[
                    {
                        "type": "function_call",
                        "name": "mail_send",
                        "arguments": '{"to":"alice@example.com"}',
                        "call_id": "call_1",
                    }
                ],
            )
        ]
    )

    result = asyncio.run(adapter.run_turn(client, "gpt-4.1", [{"role": "user", "content": "send"}], session))

    payload = decode_tool_output(result.submitted_outputs[0]["output"])
    assert payload == {"ok": False, "error": "approval_required"}


def test_auto_falls_back_to_chat_completions_for_non_responses_base_url() -> None:
    adapter, session = build_adapter()
    client = FakeClient(
        responses=[],
        response_error=RuntimeError("404 not found: /responses"),
        chat_responses=[
            make_chat_response(
                tool_calls=[
                    SimpleNamespace(
                        id="call_1",
                        function=SimpleNamespace(name="web_search", arguments='{"q":"compact tools"}'),
                    )
                ],
                response_id="chat_1",
            )
        ],
    )

    result = asyncio.run(adapter.run_turn(client, "deepseek-chat", [{"role": "user", "content": "find docs"}], session))

    assert result.endpoint_used == "chat_completions"
    assert result.submitted_outputs[0]["role"] == "tool"
    assert client.chat.completions.create_calls[0]["tools"][0]["function"]["name"] == "web_search"


def test_chat_agent_loop_appends_assistant_and_tool_messages() -> None:
    adapter, session = build_adapter()
    client = FakeClient(
        responses=[],
        response_error=RuntimeError("404 not found: /responses"),
        chat_responses=[
            make_chat_response(
                tool_calls=[
                    SimpleNamespace(
                        id="call_1",
                        function=SimpleNamespace(name="web_search", arguments='{"q":"compact tools"}'),
                    )
                ],
                response_id="chat_1",
            ),
            make_chat_response(message_content="Here are the results.", response_id="chat_2"),
        ],
    )

    loop = AgentLoop(adapter)
    result = asyncio.run(loop.run(client, "deepseek-chat", [{"role": "user", "content": "find docs"}], session))

    assert result.endpoint_used == "chat_completions"
    assert result.final_output_text == "Here are the results."
    second_messages = client.chat.completions.create_calls[1]["messages"]
    assert second_messages[-2]["role"] == "assistant"
    assert second_messages[-1]["role"] == "tool"
