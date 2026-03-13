from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Literal

from ..codec import encode_tool_output
from ..canonical_protocol import CTPEvent, CallRequest, CallResult, SessionState
from ..canonical_runtime import RuntimeExecutor, ToolRegistry
from ..canonical_schema import OpenAIStrictSchemaCompiler, normalize_tool_name


def _get_value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


@dataclass
class TurnResult:
    response_id: str | None
    raw_response: Any
    call_requests: list[CallRequest] = field(default_factory=list)
    call_results: list[CallResult] = field(default_factory=list)
    submitted_outputs: list[dict[str, Any]] = field(default_factory=list)
    final_output_text: str | None = None
    endpoint_used: str | None = None
    assistant_message: dict[str, Any] | None = None

    @property
    def has_function_calls(self) -> bool:
        return bool(self.call_requests)


class OpenAIResponsesAdapter:
    profile_name = "CTP-OAI/1"

    def __init__(
        self,
        registry: ToolRegistry,
        executor: RuntimeExecutor,
        *,
        compiler: OpenAIStrictSchemaCompiler | None = None,
        tool_limit: int = 16,
        api_style: Literal["auto", "responses", "chat_completions"] = "auto",
    ) -> None:
        self.registry = registry
        self.executor = executor
        self.compiler = compiler or OpenAIStrictSchemaCompiler()
        self.tool_limit = tool_limit
        self.api_style = api_style
        self._tool_cache: dict[tuple[str, tuple[str, ...], bool, str], list[dict[str, Any]]] = {}
        self._name_map_cache: dict[tuple[str, tuple[str, ...]], dict[str, str]] = {}

    def compile_openai_tools(
        self,
        session: SessionState,
        *,
        tool_subset: Iterable[str] | None = None,
        strict_mode: bool = True,
        endpoint: Literal["responses", "chat_completions"] = "responses",
    ) -> list[dict[str, Any]]:
        subset_tuple = tuple(tool_subset or ())
        registry_view = self.registry.subset(list(subset_tuple) if subset_tuple else None, limit=self.tool_limit)
        session.registry_hash = registry_view.hash
        session.tool_subset = subset_tuple
        self._name_map_cache[(registry_view.hash, subset_tuple)] = {
            normalize_tool_name(tool.alias): tool.alias for tool in registry_view.tools
        }
        key = (registry_view.hash, subset_tuple, strict_mode, endpoint)
        if key not in self._tool_cache:
            tools = self.compiler.compile_registry(registry_view)
            if not strict_mode:
                for tool in tools:
                    tool["strict"] = False
            if endpoint == "chat_completions":
                tools = [_response_tool_to_chat_tool(tool) for tool in tools]
            self._tool_cache[key] = tools
        return self._tool_cache[key]

    def parse_response_calls(self, response: Any, session: SessionState) -> list[CallRequest]:
        calls: list[CallRequest] = []
        for item in _get_value(response, "output", []) or []:
            if _get_value(item, "type") != "function_call":
                continue
            name = _get_value(item, "name")
            tool = self.registry.get_by_alias(self._resolve_alias(session, name))
            arguments = _json_loads(_get_value(item, "arguments", "{}"))
            calls.append(
                CallRequest(
                    cid=session.next_cid(),
                    tool_id=tool.tool_id,
                    alias=tool.alias,
                    arguments=arguments,
                    raw_call_id=_get_value(item, "call_id"),
                )
            )
        return calls

    def parse_stream_events(self, events: Iterable[Any], session: SessionState) -> tuple[list[CallRequest], list[CTPEvent], str | None]:
        calls: dict[str, dict[str, Any]] = {}
        ctp_events: list[CTPEvent] = []
        text_parts: list[str] = []

        for event in events:
            event_type = _get_value(event, "type")
            if event_type == "response.output_item.added":
                item = _get_value(event, "item", {})
                if _get_value(item, "type") == "function_call":
                    call_id = _get_value(item, "call_id")
                    calls[call_id] = {
                        "name": _get_value(item, "name"),
                        "arguments": _get_value(item, "arguments", "") or "",
                    }
            elif event_type == "response.function_call_arguments.delta":
                call_id = _get_value(event, "call_id")
                calls.setdefault(call_id, {"name": _get_value(event, "name"), "arguments": ""})
                calls[call_id]["arguments"] += _get_value(event, "delta", "")
            elif event_type == "response.function_call_arguments.done":
                call_id = _get_value(event, "call_id")
                calls.setdefault(call_id, {"name": _get_value(event, "name"), "arguments": ""})
                done_args = _get_value(event, "arguments")
                if done_args:
                    calls[call_id]["arguments"] = done_args
            elif event_type in {"response.output_text.delta", "response.text.delta"}:
                text_parts.append(_get_value(event, "delta", ""))

        requests: list[CallRequest] = []
        for call_id, payload in calls.items():
            alias = self._resolve_alias(session, payload["name"])
            tool = self.registry.get_by_alias(alias)
            arguments = _json_loads(payload.get("arguments") or "{}")
            request = CallRequest(
                cid=session.next_cid(),
                tool_id=tool.tool_id,
                alias=tool.alias,
                arguments=arguments,
                raw_call_id=call_id,
            )
            requests.append(request)
            ctp_events.append(
                CTPEvent(
                    kind="call",
                    cid=request.cid,
                    tool_id=request.tool_id,
                    alias=request.alias,
                    payload={"arguments": request.arguments, "raw_call_id": call_id},
                )
            )

        final_text = "".join(text_parts) or None
        return requests, ctp_events, final_text

    def parse_chat_calls(self, response: Any, session: SessionState) -> tuple[list[CallRequest], dict[str, Any] | None, str | None]:
        choices = _get_value(response, "choices", []) or []
        if not choices:
            return [], None, None
        message = _get_value(choices[0], "message", {})
        assistant_message = _dump_message(message)
        text = _message_text(message)
        calls: list[CallRequest] = []
        for tool_call in _get_value(message, "tool_calls", []) or []:
            function = _get_value(tool_call, "function", {})
            name = _get_value(function, "name")
            tool = self.registry.get_by_alias(self._resolve_alias(session, name))
            arguments = _json_loads(_get_value(function, "arguments", "{}"))
            calls.append(
                CallRequest(
                    cid=session.next_cid(),
                    tool_id=tool.tool_id,
                    alias=tool.alias,
                    arguments=arguments,
                    raw_call_id=_get_value(tool_call, "id"),
                )
            )
        return calls, assistant_message, text

    def parse_chat_stream_events(self, events: Iterable[Any], session: SessionState) -> tuple[list[CallRequest], list[CTPEvent], str | None]:
        calls: dict[int, dict[str, Any]] = {}
        text_parts: list[str] = []
        ctp_events: list[CTPEvent] = []
        for chunk in events:
            for choice in _get_value(chunk, "choices", []) or []:
                delta = _get_value(choice, "delta", {})
                content = _get_value(delta, "content")
                if content:
                    if isinstance(content, list):
                        text_parts.extend(part.get("text", "") for part in content if isinstance(part, dict))
                    else:
                        text_parts.append(content)
                for tool_call in _get_value(delta, "tool_calls", []) or []:
                    index = _get_value(tool_call, "index", 0)
                    entry = calls.setdefault(index, {"id": None, "name": "", "arguments": ""})
                    call_id = _get_value(tool_call, "id")
                    if call_id:
                        entry["id"] = call_id
                    function = _get_value(tool_call, "function", {})
                    name = _get_value(function, "name")
                    if name:
                        entry["name"] = name
                    arguments = _get_value(function, "arguments")
                    if arguments:
                        entry["arguments"] += arguments

        requests: list[CallRequest] = []
        for payload in calls.values():
            alias = self._resolve_alias(session, payload["name"])
            tool = self.registry.get_by_alias(alias)
            arguments = _json_loads(payload.get("arguments") or "{}")
            request = CallRequest(
                cid=session.next_cid(),
                tool_id=tool.tool_id,
                alias=tool.alias,
                arguments=arguments,
                raw_call_id=payload.get("id"),
            )
            requests.append(request)
            ctp_events.append(
                CTPEvent(
                    kind="call",
                    cid=request.cid,
                    tool_id=request.tool_id,
                    alias=request.alias,
                    payload={"arguments": request.arguments, "raw_call_id": request.raw_call_id},
                )
            )
        return requests, ctp_events, "".join(text_parts) or None

    async def run_turn(
        self,
        client: Any,
        model: str,
        input_items: list[dict[str, Any]],
        session: SessionState,
        *,
        tool_subset: Iterable[str] | None = None,
        previous_response_id: str | None = None,
        strict_mode: bool = True,
        extra_create_args: dict[str, Any] | None = None,
    ) -> TurnResult:
        endpoint = self._choose_endpoint(client)
        if endpoint == "responses":
            try:
                return await self._run_responses_turn(
                    client,
                    model,
                    input_items,
                    session,
                    tool_subset=tool_subset,
                    previous_response_id=previous_response_id,
                    strict_mode=strict_mode,
                    extra_create_args=extra_create_args,
                )
            except Exception as exc:
                if self.api_style != "auto" or not _should_fallback_to_chat(exc):
                    raise
                endpoint = "chat_completions"
        return await self._run_chat_turn(
            client,
            model,
            input_items,
            session,
            tool_subset=tool_subset,
            strict_mode=strict_mode,
            extra_create_args=extra_create_args,
        )

    async def stream_turn(
        self,
        client: Any,
        model: str,
        input_items: list[dict[str, Any]],
        session: SessionState,
        *,
        tool_subset: Iterable[str] | None = None,
        previous_response_id: str | None = None,
        strict_mode: bool = True,
        extra_create_args: dict[str, Any] | None = None,
    ) -> Iterator[CTPEvent]:
        endpoint = self._choose_endpoint(client)
        if endpoint == "responses":
            try:
                async for event in self._stream_responses_turn(
                    client,
                    model,
                    input_items,
                    session,
                    tool_subset=tool_subset,
                    previous_response_id=previous_response_id,
                    strict_mode=strict_mode,
                    extra_create_args=extra_create_args,
                ):
                    yield event
                return
            except Exception as exc:
                if self.api_style != "auto" or not _should_fallback_to_chat(exc):
                    raise
        async for event in self._stream_chat_turn(
            client,
            model,
            input_items,
            session,
            tool_subset=tool_subset,
            strict_mode=strict_mode,
            extra_create_args=extra_create_args,
        ):
            yield event

    async def _run_responses_turn(
        self,
        client: Any,
        model: str,
        input_items: list[dict[str, Any]],
        session: SessionState,
        *,
        tool_subset: Iterable[str] | None = None,
        previous_response_id: str | None = None,
        strict_mode: bool = True,
        extra_create_args: dict[str, Any] | None = None,
    ) -> TurnResult:
        tools = self.compile_openai_tools(
            session, tool_subset=tool_subset, strict_mode=strict_mode, endpoint="responses"
        )
        response = client.responses.create(
            model=model,
            input=input_items,
            tools=tools,
            previous_response_id=previous_response_id or session.openai_response_id,
            **(extra_create_args or {}),
        )
        session.openai_response_id = _get_value(response, "id")
        call_requests = self.parse_response_calls(response, session)
        if not call_requests:
            return TurnResult(
                response_id=session.openai_response_id,
                raw_response=response,
                final_output_text=_extract_output_text(response),
                endpoint_used="responses",
            )
        call_results = await self.executor.execute_many(call_requests)
        submitted = submit_tool_results(session, call_results, endpoint="responses")
        return TurnResult(
            response_id=session.openai_response_id,
            raw_response=response,
            call_requests=call_requests,
            call_results=call_results,
            submitted_outputs=submitted,
            endpoint_used="responses",
        )

    async def _run_chat_turn(
        self,
        client: Any,
        model: str,
        input_items: list[dict[str, Any]],
        session: SessionState,
        *,
        tool_subset: Iterable[str] | None = None,
        strict_mode: bool = True,
        extra_create_args: dict[str, Any] | None = None,
    ) -> TurnResult:
        tools = self.compile_openai_tools(
            session, tool_subset=tool_subset, strict_mode=strict_mode, endpoint="chat_completions"
        )
        response = client.chat.completions.create(
            model=model,
            messages=input_items,
            tools=tools,
            **(extra_create_args or {}),
        )
        call_requests, assistant_message, final_text = self.parse_chat_calls(response, session)
        if not call_requests:
            return TurnResult(
                response_id=_get_value(response, "id"),
                raw_response=response,
                final_output_text=final_text,
                endpoint_used="chat_completions",
                assistant_message=assistant_message,
            )
        call_results = await self.executor.execute_many(call_requests)
        submitted = submit_tool_results(session, call_results, endpoint="chat_completions")
        return TurnResult(
            response_id=_get_value(response, "id"),
            raw_response=response,
            call_requests=call_requests,
            call_results=call_results,
            submitted_outputs=submitted,
            endpoint_used="chat_completions",
            assistant_message=assistant_message,
        )

    async def _stream_responses_turn(
        self,
        client: Any,
        model: str,
        input_items: list[dict[str, Any]],
        session: SessionState,
        *,
        tool_subset: Iterable[str] | None = None,
        previous_response_id: str | None = None,
        strict_mode: bool = True,
        extra_create_args: dict[str, Any] | None = None,
    ) -> Iterator[CTPEvent]:
        tools = self.compile_openai_tools(
            session, tool_subset=tool_subset, strict_mode=strict_mode, endpoint="responses"
        )
        with client.responses.stream(
            model=model,
            input=input_items,
            tools=tools,
            previous_response_id=previous_response_id or session.openai_response_id,
            **(extra_create_args or {}),
        ) as stream:
            events = list(stream)
        requests, ctp_events, final_text = self.parse_stream_events(events, session)
        for event in ctp_events:
            yield event
        if not requests:
            if final_text is not None:
                yield CTPEvent(kind="final_text", payload={"text": final_text, "endpoint": "responses"})
            return
        results = await self.executor.execute_many(requests)
        for result in results:
            yield _result_event(result)
        yield CTPEvent(kind="done", payload={"endpoint": "responses"})

    async def _stream_chat_turn(
        self,
        client: Any,
        model: str,
        input_items: list[dict[str, Any]],
        session: SessionState,
        *,
        tool_subset: Iterable[str] | None = None,
        strict_mode: bool = True,
        extra_create_args: dict[str, Any] | None = None,
    ) -> Iterator[CTPEvent]:
        tools = self.compile_openai_tools(
            session, tool_subset=tool_subset, strict_mode=strict_mode, endpoint="chat_completions"
        )
        stream = client.chat.completions.create(
            model=model,
            messages=input_items,
            tools=tools,
            stream=True,
            **(extra_create_args or {}),
        )
        requests, ctp_events, final_text = self.parse_chat_stream_events(stream, session)
        for event in ctp_events:
            yield event
        if not requests:
            if final_text is not None:
                yield CTPEvent(kind="final_text", payload={"text": final_text, "endpoint": "chat_completions"})
            return
        results = await self.executor.execute_many(requests)
        for result in results:
            yield _result_event(result)
        yield CTPEvent(kind="done", payload={"endpoint": "chat_completions"})

    def _choose_endpoint(self, client: Any) -> Literal["responses", "chat_completions"]:
        if self.api_style == "responses":
            return "responses"
        if self.api_style == "chat_completions":
            return "chat_completions"
        if hasattr(client, "responses"):
            return "responses"
        return "chat_completions"

    def _resolve_alias(self, session: SessionState, name: str) -> str:
        mapping = self._name_map_cache.get((session.registry_hash, session.tool_subset), {})
        return mapping.get(name, name)


class AgentLoop:
    def __init__(self, adapter: OpenAIResponsesAdapter, *, max_tool_rounds: int = 8) -> None:
        self.adapter = adapter
        self.max_tool_rounds = max_tool_rounds

    async def run(
        self,
        client: Any,
        model: str,
        input_items: list[dict[str, Any]],
        session: SessionState,
        *,
        tool_subset: Iterable[str] | None = None,
        strict_mode: bool = True,
        extra_create_args: dict[str, Any] | None = None,
    ) -> TurnResult:
        current_input = list(input_items)
        previous_response_id: str | None = None
        last_result: TurnResult | None = None
        for _ in range(self.max_tool_rounds):
            last_result = await self.adapter.run_turn(
                client,
                model,
                current_input,
                session,
                tool_subset=tool_subset,
                previous_response_id=previous_response_id,
                strict_mode=strict_mode,
                extra_create_args=extra_create_args,
            )
            if not last_result.has_function_calls:
                return last_result
            if last_result.endpoint_used == "chat_completions":
                current_input = [
                    *current_input,
                    *( [last_result.assistant_message] if last_result.assistant_message else [] ),
                    *last_result.submitted_outputs,
                ]
            else:
                current_input = last_result.submitted_outputs
                previous_response_id = last_result.response_id
        raise RuntimeError("max_tool_rounds exceeded")


OpenAIAdapter = OpenAIResponsesAdapter


def compile_openai_tools(
    registry_view: Any,
    compiler: OpenAIStrictSchemaCompiler | None = None,
    *,
    endpoint: Literal["responses", "chat_completions"] = "responses",
) -> list[dict[str, Any]]:
    compiler = compiler or OpenAIStrictSchemaCompiler()
    tools = compiler.compile_registry(registry_view)
    if endpoint == "chat_completions":
        return [_response_tool_to_chat_tool(tool) for tool in tools]
    return tools


async def run_responses_turn(
    client: Any,
    model: str,
    input_items: list[dict[str, Any]],
    session: SessionState,
    *,
    adapter: OpenAIResponsesAdapter,
    tool_subset: Iterable[str] | None = None,
    strict_mode: bool = True,
    extra_create_args: dict[str, Any] | None = None,
) -> TurnResult:
    return await adapter.run_turn(
        client,
        model,
        input_items,
        session,
        tool_subset=tool_subset,
        strict_mode=strict_mode,
        extra_create_args=extra_create_args,
    )


async def stream_responses_turn(
    client: Any,
    model: str,
    input_items: list[dict[str, Any]],
    session: SessionState,
    *,
    adapter: OpenAIResponsesAdapter,
    tool_subset: Iterable[str] | None = None,
    strict_mode: bool = True,
    extra_create_args: dict[str, Any] | None = None,
) -> Iterator[CTPEvent]:
    async for event in adapter.stream_turn(
        client,
        model,
        input_items,
        session,
        tool_subset=tool_subset,
        strict_mode=strict_mode,
        extra_create_args=extra_create_args,
    ):
        yield event


def submit_tool_results(
    session: SessionState,
    results: list[CallResult],
    *,
    endpoint: Literal["responses", "chat_completions"] = "responses",
) -> list[dict[str, Any]]:
    del session
    output_items: list[dict[str, Any]] = []
    for result in results:
        if result.raw_call_id is None:
            continue
        if endpoint == "chat_completions":
            output_items.append(
                {
                    "role": "tool",
                    "tool_call_id": result.raw_call_id,
                    "content": encode_tool_output(result),
                }
            )
        else:
            output_items.append(
                {
                    "type": "function_call_output",
                    "call_id": result.raw_call_id,
                    "output": encode_tool_output(result),
                }
            )
    return output_items


def _extract_output_text(response: Any) -> str | None:
    output_text = _get_value(response, "output_text")
    if output_text:
        return output_text
    for item in _get_value(response, "output", []) or []:
        if _get_value(item, "type") == "message":
            content = _get_value(item, "content", []) or []
            parts = [
                _get_value(part, "text")
                for part in content
                if _get_value(part, "type") in {"output_text", "text"}
            ]
            joined = "".join(part for part in parts if part)
            if joined:
                return joined
    return None


def _json_loads(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    return json.loads(raw)


def _response_tool_to_chat_tool(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["parameters"],
            "strict": tool.get("strict", True),
        },
    }


def _dump_message(message: Any) -> dict[str, Any]:
    if hasattr(message, "model_dump"):
        return message.model_dump(exclude_none=True)
    if isinstance(message, dict):
        return {key: value for key, value in message.items() if value is not None}
    return {}


def _message_text(message: Any) -> str | None:
    content = _get_value(message, "content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(text)
        return "".join(parts) or None
    return None


def _result_event(result: CallResult) -> CTPEvent:
    payload = {"status": result.status, "raw_call_id": result.raw_call_id}
    if result.status == "ok":
        payload["summary"] = result.summary
        if result.handle:
            payload["handle"] = result.handle.id
    else:
        payload["error"] = result.error.code if result.error else "exec:error"
    return CTPEvent(kind="result", cid=result.cid, payload=payload)


def _should_fallback_to_chat(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    message = str(exc).lower()
    if status_code in {400, 404, 405, 415, 501}:
        return True
    return any(
        marker in message
        for marker in (
            "/responses",
            "responses api",
            "unsupported endpoint",
            "unsupported",
            "not found",
            "unknown url",
            "does not exist",
        )
    )
