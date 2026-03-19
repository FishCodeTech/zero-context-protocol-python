from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from openai import OpenAI

DEFAULT_API_KEY = os.environ.get("OPENAI_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "")
DEFAULT_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com")
DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "deepseek-chat")
DEFAULT_REPEATS = 2
MAX_TOOL_ROUNDS = 8

WEATHER_DATA: dict[str, dict[str, Any]] = {
    "beijing": {"temperature_c": 18.0, "condition": "Sunny", "humidity": 35},
    "shanghai": {"temperature_c": 22.0, "condition": "Rain", "humidity": 81},
    "hangzhou": {"temperature_c": 24.0, "condition": "Cloudy", "humidity": 67},
    "shenzhen": {"temperature_c": 27.0, "condition": "Thunderstorms", "humidity": 84},
}


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    prompt: str
    required_tool_calls: dict[str, int]
    expected: dict[str, Any]


@dataclass
class UsageTotals:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def add(self, prompt_tokens: int, completion_tokens: int, total_tokens: int) -> None:
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.total_tokens += total_tokens


@dataclass
class RunRecord:
    protocol: str
    case_id: str
    repeat_index: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    answer_ok: bool
    tool_ok: bool
    turns: int
    tool_history: list[str]
    final_text: str
    parsed_output: dict[str, Any] | None
    error: str | None = None


@dataclass
class ProtocolSummary:
    protocol: str
    runs: int
    answer_accuracy: float
    tool_compliance: float
    avg_prompt_tokens: float
    avg_completion_tokens: float
    avg_total_tokens: float


@dataclass(frozen=True)
class ProgressEvent:
    protocol: str
    repeat_index: int
    total_repeats: int
    case_index: int
    total_cases: int
    overall_index: int
    overall_total: int
    phase: str
    case_id: str
    elapsed_seconds: float | None = None
    total_tokens: int | None = None
    answer_ok: bool | None = None
    tool_ok: bool | None = None
    turns: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    source_name: str
    exposed_name: str
    description: str
    input_schema: dict[str, Any]
    metadata: dict[str, Any] | None = None


def benchmark_cases() -> list[BenchmarkCase]:
    return [
        BenchmarkCase(
            case_id="warmer_city_delta",
            prompt=(
                "查询杭州和北京的天气，判断哪个城市温度更高，并给出温差（摄氏度）。"
                "最终只输出一行 JSON，格式为"
                ' {"warmer_city":"城市名","delta_c":数字}。'
            ),
            required_tool_calls={"weather": 2, "subtract": 1},
            expected={"warmer_city": "Hangzhou", "delta_c": 6.0},
        ),
        BenchmarkCase(
            case_id="shanghai_temp_f_and_humidity",
            prompt=(
                "查询上海天气，把温度转换为华氏度，并返回湿度。"
                "最终只输出一行 JSON，格式为"
                ' {"city":"城市名","temp_f":数字,"humidity":数字}。'
            ),
            required_tool_calls={"weather": 1, "convert": 1},
            expected={"city": "Shanghai", "temp_f": 71.6, "humidity": 81},
        ),
        BenchmarkCase(
            case_id="average_three_city_temperature",
            prompt=(
                "查询北京、上海、深圳的天气，计算三地平均温度（摄氏度），保留一位小数。"
                "最终只输出一行 JSON，格式为"
                ' {"avg_temp_c":数字,"cities":["Beijing","Shanghai","Shenzhen"]}。'
            ),
            required_tool_calls={"weather": 3, "average": 1},
            expected={"avg_temp_c": 22.3, "cities": ["Beijing", "Shanghai", "Shenzhen"]},
        ),
        BenchmarkCase(
            case_id="more_humid_city_delta",
            prompt=(
                "查询深圳和杭州的天气，判断哪个城市湿度更高，并给出湿度差。"
                "最终只输出一行 JSON，格式为"
                ' {"more_humid_city":"城市名","humidity_delta":数字}。'
            ),
            required_tool_calls={"weather": 2, "subtract": 1},
            expected={"more_humid_city": "Shenzhen", "humidity_delta": 17.0},
        ),
    ]


def make_openai_client(*, api_key: str = DEFAULT_API_KEY, base_url: str = DEFAULT_BASE_URL) -> OpenAI:
    if not api_key:
        raise ValueError(
            "api_key is required. Set OPENAI_API_KEY (or DEEPSEEK_API_KEY) or pass --api-key explicitly."
        )
    return OpenAI(api_key=api_key, base_url=base_url)


def _canonical_city(value: str) -> str:
    return value.strip().lower()


def _title_city(value: str) -> str:
    return value.strip().title()


def _canonical_city_label(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip().lower()
    mapping = {
        "beijing": "beijing",
        "北京": "beijing",
        "shanghai": "shanghai",
        "上海": "shanghai",
        "hangzhou": "hangzhou",
        "杭州": "hangzhou",
        "shenzhen": "shenzhen",
        "深圳": "shenzhen",
    }
    return mapping.get(raw)


def build_zcp_app():
    from zcp import FastZCP

    app = FastZCP("benchmark-zcp")

    @app.tool(
        name="weather.get_current",
        description="Get the current weather for one city.",
        input_schema={
            "type": "object",
            "properties": {
                "city": {"type": "string", "enum": ["Beijing", "Shanghai", "Hangzhou", "Shenzhen"]},
                "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
            },
            "required": ["city"],
            "additionalProperties": False,
        },
        output_mode="scalar",
        inline_ok=True,
    )
    def get_weather(city: str, unit: str = "celsius") -> dict[str, Any]:
        data = WEATHER_DATA[_canonical_city(city)]
        temperature_c = float(data["temperature_c"])
        temperature = temperature_c if unit == "celsius" else round((temperature_c * 9 / 5) + 32, 1)
        return {
            "city": _title_city(city),
            "unit": unit,
            "temperature": temperature,
            "condition": data["condition"],
            "humidity": int(data["humidity"]),
        }

    @app.tool(
        name="math.subtract",
        description="Subtract b from a.",
        input_schema={
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["a", "b"],
            "additionalProperties": False,
        },
        output_mode="scalar",
        inline_ok=True,
    )
    def subtract(a: float, b: float) -> dict[str, float]:
        return {"result": round(float(a) - float(b), 1)}

    @app.tool(
        name="math.average",
        description="Average a list of numbers.",
        input_schema={
            "type": "object",
            "properties": {
                "values": {"type": "array", "items": {"type": "number"}},
            },
            "required": ["values"],
            "additionalProperties": False,
        },
        output_mode="scalar",
        inline_ok=True,
    )
    def average(values: list[float]) -> dict[str, float]:
        parsed = [float(value) for value in values]
        return {"result": round(sum(parsed) / len(parsed), 1)}

    @app.tool(
        name="unit.celsius_to_fahrenheit",
        description="Convert Celsius to Fahrenheit.",
        input_schema={
            "type": "object",
            "properties": {"celsius": {"type": "number"}},
            "required": ["celsius"],
            "additionalProperties": False,
        },
        output_mode="scalar",
        inline_ok=True,
    )
    def convert(celsius: float) -> dict[str, float]:
        return {"result": round((float(celsius) * 9 / 5) + 32, 1)}

    return app


class ZCPRealBackend:
    protocol = "zcp"

    def __init__(self) -> None:
        self._client = None

    async def __aenter__(self) -> "ZCPRealBackend":
        from zcp import stdio_client, stdio_server

        app = build_zcp_app()
        self._server = stdio_server(app)
        self._client = stdio_client(self._server)
        await self._client.initialize()
        await self._client.initialized()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def list_tools(self) -> list[ToolSpec]:
        result = await self._client.list_tools()
        return [
            ToolSpec(
                source_name=tool["name"],
                exposed_name=normalize_tool_name(tool["name"]),
                description=tool["description"],
                input_schema=tool["inputSchema"],
            )
            for tool in result["tools"]
        ]

    async def call_tool(self, source_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._client.call_tool(source_name, arguments)


class MCPRealBackend:
    protocol = "mcp"

    def __init__(self, *, python_executable: str, server_script: str) -> None:
        self.python_executable = python_executable
        self.server_script = server_script
        self._stdio_cm = None
        self._session_cm = None
        self._session = None

    async def __aenter__(self) -> "MCPRealBackend":
        from mcp.client.session import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        self._stdio_cm = stdio_client(
            StdioServerParameters(
                command=self.python_executable,
                args=[self.server_script],
                cwd=str(Path(self.server_script).resolve().parents[1]),
            )
        )
        read_stream, write_stream = await self._stdio_cm.__aenter__()
        self._session_cm = ClientSession(read_stream, write_stream)
        self._session = await self._session_cm.__aenter__()
        await self._session.initialize()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._session_cm is not None:
            await self._session_cm.__aexit__(exc_type, exc, tb)
        if self._stdio_cm is not None:
            await self._stdio_cm.__aexit__(exc_type, exc, tb)

    async def list_tools(self) -> list[ToolSpec]:
        result = await self._session.list_tools()
        return [
            ToolSpec(
                source_name=tool.name,
                exposed_name=normalize_tool_name(tool.name),
                description=tool.description or tool.title or tool.name,
                input_schema=tool.input_schema,
            )
            for tool in result.tools
        ]

    async def call_tool(self, source_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self._session.call_tool(source_name, arguments)
        return result.model_dump(exclude_none=True, by_alias=True)


def normalize_tool_name(name: str) -> str:
    return name.replace(".", "_").replace("-", "_")


def _system_prompt() -> str:
    return (
        "你是一个严格遵循工具调用规则的助手。"
        " 只要任务涉及天气事实、温度转换、减法或平均值，就必须调用对应工具，不能心算或凭记忆回答。"
        " 如果需要查询多个城市或执行多个独立工具步骤，优先在同一轮里并行发起多个工具调用，不要无谓拆成多轮。"
        " 最终答案只能输出单行 JSON，不能加解释、Markdown 或代码块。"
    )


def _conversation_start(case: BenchmarkCase) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": case.prompt},
    ]


def _extract_usage(response: Any) -> UsageTotals:
    usage = _get_value(response, "usage", {}) or {}
    prompt_tokens = int(_get_value(usage, "prompt_tokens", _get_value(usage, "input_tokens", 0)) or 0)
    completion_tokens = int(_get_value(usage, "completion_tokens", _get_value(usage, "output_tokens", 0)) or 0)
    total_tokens = int(_get_value(usage, "total_tokens", prompt_tokens + completion_tokens) or 0)
    return UsageTotals(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, total_tokens=total_tokens)


def _get_value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _message_text(message: Any) -> str:
    content = _get_value(message, "content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("text"):
                text_parts.append(str(item["text"]))
        return "".join(text_parts)
    return ""


def _dump_message(message: Any) -> dict[str, Any]:
    if hasattr(message, "model_dump"):
        return message.model_dump(exclude_none=True)
    if isinstance(message, dict):
        return {key: value for key, value in message.items() if value is not None}
    return {}


def _build_openai_tools(tool_specs: list[ToolSpec]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    mapping = {tool.exposed_name: tool.source_name for tool in tool_specs}
    tools = [
        {
            "type": "function",
            "function": {
                "name": tool.exposed_name,
                "description": tool.description,
                "parameters": tool.input_schema,
                "strict": True,
            },
        }
        for tool in tool_specs
    ]
    return tools, mapping


def _tool_output_for_model(protocol: str, payload: dict[str, Any]) -> str:
    del protocol
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _tool_kind(protocol: str, tool_name: str) -> str | None:
    if protocol == "zcp":
        mapping = {
            "weather.get_current": "weather",
            "math.subtract": "subtract",
            "math.average": "average",
            "unit.celsius_to_fahrenheit": "convert",
        }
    else:
        mapping = {
            "get_weather": "weather",
            "subtract_numbers": "subtract",
            "average_numbers": "average",
            "convert_celsius_to_fahrenheit": "convert",
        }
    return mapping.get(tool_name)


async def _run_case_with_backend(
    backend: Any,
    *,
    client: OpenAI,
    model: str,
    case: BenchmarkCase,
    temperature: float,
) -> RunRecord:
    usage = UsageTotals()
    tool_history: list[str] = []
    tool_specs = await backend.list_tools()
    tools, exposed_to_source = _build_openai_tools(tool_specs)
    messages = _conversation_start(case)

    for turn_index in range(1, MAX_TOOL_ROUNDS + 1):
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
        )
        turn_usage = _extract_usage(response)
        usage.add(turn_usage.prompt_tokens, turn_usage.completion_tokens, turn_usage.total_tokens)
        choices = _get_value(response, "choices", []) or []
        if not choices:
            return RunRecord(
                protocol=backend.protocol,
                case_id=case.case_id,
                repeat_index=0,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
                answer_ok=False,
                tool_ok=False,
                turns=turn_index,
                tool_history=tool_history,
                final_text="",
                parsed_output=None,
                error="empty_choices",
            )
        message = _get_value(choices[0], "message", {})
        tool_calls = _get_value(message, "tool_calls", []) or []
        if not tool_calls:
            final_text = _message_text(message)
            parsed = parse_json_object(final_text)
            return _finalize_record(
                protocol=backend.protocol,
                case=case,
                repeat_index=0,
                usage=usage,
                turns=turn_index,
                tool_history=tool_history,
                final_text=final_text,
                parsed=parsed,
            )

        messages.append(_dump_message(message))
        for tool_call in tool_calls:
            function = _get_value(tool_call, "function", {})
            exposed_name = str(_get_value(function, "name", ""))
            source_name = exposed_to_source[exposed_name]
            arguments = json.loads(str(_get_value(function, "arguments", "{}") or "{}"))
            try:
                payload = await backend.call_tool(source_name, arguments)
            except Exception as exc:
                payload = {"isError": True, "error": str(exc)}
            tool_history.append(source_name)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": _get_value(tool_call, "id"),
                    "content": _tool_output_for_model(backend.protocol, payload),
                }
            )

    return RunRecord(
        protocol=backend.protocol,
        case_id=case.case_id,
        repeat_index=0,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        answer_ok=False,
        tool_ok=False,
        turns=MAX_TOOL_ROUNDS,
        tool_history=tool_history,
        final_text="",
        parsed_output=None,
        error="max_tool_rounds_exceeded",
    )


async def _run_protocol_benchmark_async(
    protocol: str,
    *,
    client: OpenAI,
    model: str,
    repeats: int,
    temperature: float,
    python_executable: str | None,
    mcp_server_script: str | None,
    progress: Callable[[ProgressEvent], None] | None,
) -> list[RunRecord]:
    runs: list[RunRecord] = []
    cases = benchmark_cases()
    overall_total = repeats * len(cases)
    overall_index = 0
    backend: Any
    if protocol == "zcp":
        backend = ZCPRealBackend()
    elif protocol == "mcp":
        if python_executable is None or mcp_server_script is None:
            raise ValueError("python_executable and mcp_server_script are required for mcp benchmark")
        backend = MCPRealBackend(python_executable=python_executable, server_script=mcp_server_script)
    else:
        raise ValueError(f"unsupported protocol {protocol!r}")

    async with backend:
        for repeat_index in range(1, repeats + 1):
            for case_index, case in enumerate(cases, start=1):
                overall_index += 1
                if progress is not None:
                    progress(
                        ProgressEvent(
                            protocol=protocol,
                            repeat_index=repeat_index,
                            total_repeats=repeats,
                            case_index=case_index,
                            total_cases=len(cases),
                            overall_index=overall_index,
                            overall_total=overall_total,
                            phase="start",
                            case_id=case.case_id,
                        )
                    )
                started_at = time.perf_counter()
                record = await _run_case_with_backend(
                    backend,
                    client=client,
                    model=model,
                    case=case,
                    temperature=temperature,
                )
                record.repeat_index = repeat_index
                runs.append(record)
                if progress is not None:
                    progress(
                        ProgressEvent(
                            protocol=protocol,
                            repeat_index=repeat_index,
                            total_repeats=repeats,
                            case_index=case_index,
                            total_cases=len(cases),
                            overall_index=overall_index,
                            overall_total=overall_total,
                            phase="done",
                            case_id=case.case_id,
                            elapsed_seconds=time.perf_counter() - started_at,
                            total_tokens=record.total_tokens,
                            answer_ok=record.answer_ok,
                            tool_ok=record.tool_ok,
                            turns=record.turns,
                            error=record.error,
                        )
                    )
    return runs


def run_protocol_benchmark(
    protocol: str,
    *,
    client: OpenAI | None = None,
    api_key: str = DEFAULT_API_KEY,
    base_url: str = DEFAULT_BASE_URL,
    model: str = DEFAULT_MODEL,
    repeats: int = DEFAULT_REPEATS,
    temperature: float = 0.0,
    progress: Callable[[ProgressEvent], None] | None = None,
    python_executable: str | None = None,
    mcp_server_script: str | None = None,
) -> list[RunRecord]:
    client = client or make_openai_client(api_key=api_key, base_url=base_url)
    return asyncio.run(
        _run_protocol_benchmark_async(
            protocol,
            client=client,
            model=model,
            repeats=repeats,
            temperature=temperature,
            python_executable=python_executable,
            mcp_server_script=mcp_server_script,
            progress=progress,
        )
    )


def parse_json_object(text: str) -> dict[str, Any] | None:
    raw = text.strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = raw.strip("`")
        if "\n" in raw:
            raw = raw.split("\n", 1)[1]
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        value = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _float_close(left: Any, right: Any, tolerance: float = 0.15) -> bool:
    try:
        return abs(float(left) - float(right)) <= tolerance
    except (TypeError, ValueError):
        return False


def evaluate_case_output(case: BenchmarkCase, parsed: dict[str, Any] | None) -> bool:
    if parsed is None:
        return False
    expected = case.expected
    if case.case_id == "warmer_city_delta":
        return _canonical_city_label(parsed.get("warmer_city")) == _canonical_city_label(expected["warmer_city"]) and _float_close(
            parsed.get("delta_c"), expected["delta_c"]
        )
    if case.case_id == "shanghai_temp_f_and_humidity":
        return (
            _canonical_city_label(parsed.get("city")) == _canonical_city_label(expected["city"])
            and _float_close(parsed.get("temp_f"), expected["temp_f"])
            and int(parsed.get("humidity", -1)) == expected["humidity"]
        )
    if case.case_id == "average_three_city_temperature":
        parsed_cities = [_canonical_city_label(item) for item in parsed.get("cities", [])]
        expected_cities = [_canonical_city_label(item) for item in expected["cities"]]
        return parsed_cities == expected_cities and _float_close(parsed.get("avg_temp_c"), expected["avg_temp_c"])
    if case.case_id == "more_humid_city_delta":
        return _canonical_city_label(parsed.get("more_humid_city")) == _canonical_city_label(
            expected["more_humid_city"]
        ) and _float_close(parsed.get("humidity_delta"), expected["humidity_delta"])
    return False


def evaluate_tool_history(protocol: str, case: BenchmarkCase, tool_history: Iterable[str]) -> bool:
    counts: dict[str, int] = {}
    for item in tool_history:
        kind = _tool_kind(protocol, item)
        if kind is None:
            continue
        counts[kind] = counts.get(kind, 0) + 1
    return all(counts.get(kind, 0) >= expected_count for kind, expected_count in case.required_tool_calls.items())


def _finalize_record(
    *,
    protocol: str,
    case: BenchmarkCase,
    repeat_index: int,
    usage: UsageTotals,
    turns: int,
    tool_history: list[str],
    final_text: str,
    parsed: dict[str, Any] | None,
) -> RunRecord:
    answer_ok = evaluate_case_output(case, parsed)
    tool_ok = evaluate_tool_history(protocol, case, tool_history)
    return RunRecord(
        protocol=protocol,
        case_id=case.case_id,
        repeat_index=repeat_index,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        answer_ok=answer_ok,
        tool_ok=tool_ok,
        turns=turns,
        tool_history=tool_history,
        final_text=final_text,
        parsed_output=parsed,
        error=None,
    )


def summarize_runs(runs: list[RunRecord]) -> list[ProtocolSummary]:
    grouped: dict[str, list[RunRecord]] = {}
    for run in runs:
        grouped.setdefault(run.protocol, []).append(run)
    summaries: list[ProtocolSummary] = []
    for protocol, items in sorted(grouped.items()):
        count = len(items)
        summaries.append(
            ProtocolSummary(
                protocol=protocol,
                runs=count,
                answer_accuracy=(sum(1 for item in items if item.answer_ok) / count) if count else 0.0,
                tool_compliance=(sum(1 for item in items if item.tool_ok) / count) if count else 0.0,
                avg_prompt_tokens=(sum(item.prompt_tokens for item in items) / count) if count else 0.0,
                avg_completion_tokens=(sum(item.completion_tokens for item in items) / count) if count else 0.0,
                avg_total_tokens=(sum(item.total_tokens for item in items) / count) if count else 0.0,
            )
        )
    return summaries


def case_breakdown(runs: list[RunRecord]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[RunRecord]] = {}
    for run in runs:
        grouped.setdefault((run.case_id, run.protocol), []).append(run)
    rows: list[dict[str, Any]] = []
    for case in benchmark_cases():
        row: dict[str, Any] = {"case_id": case.case_id}
        for protocol in ("zcp", "mcp"):
            items = grouped.get((case.case_id, protocol), [])
            if items:
                row[f"{protocol}_avg_total_tokens"] = sum(item.total_tokens for item in items) / len(items)
                row[f"{protocol}_accuracy"] = sum(1 for item in items if item.answer_ok) / len(items)
                row[f"{protocol}_tool_compliance"] = sum(1 for item in items if item.tool_ok) / len(items)
            else:
                row[f"{protocol}_avg_total_tokens"] = 0.0
                row[f"{protocol}_accuracy"] = 0.0
                row[f"{protocol}_tool_compliance"] = 0.0
        zcp_tokens = row["zcp_avg_total_tokens"]
        mcp_tokens = row["mcp_avg_total_tokens"]
        row["mcp_vs_zcp_ratio"] = (mcp_tokens / zcp_tokens) if zcp_tokens else 0.0
        row["token_delta_mcp_minus_zcp"] = mcp_tokens - zcp_tokens
        rows.append(row)
    return rows


def markdown_report(runs: list[RunRecord], *, model: str, repeats: int) -> str:
    summaries = summarize_runs(runs)
    cases = case_breakdown(runs)
    lines = [
        "# ZCP vs MCP Real SDK Tool-Call Benchmark",
        "",
        f"- model: `{model}`",
        f"- repeats: `{repeats}`",
        f"- cases per protocol: `{len(benchmark_cases())}`",
        "",
        "## Summary",
        "",
        "| Protocol | Runs | Answer Accuracy | Tool Compliance | Avg Prompt Tokens | Avg Completion Tokens | Avg Total Tokens |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for summary in summaries:
        lines.append(
            "| {protocol} | {runs} | {answer_accuracy:.1%} | {tool_compliance:.1%} | {avg_prompt_tokens:.1f} | {avg_completion_tokens:.1f} | {avg_total_tokens:.1f} |".format(
                **asdict(summary)
            )
        )

    lines.extend(
        [
            "",
            "## Case Breakdown",
            "",
            "| Case | ZCP Avg Total | MCP Avg Total | MCP-ZCP | MCP / ZCP | ZCP Accuracy | MCP Accuracy |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in cases:
        lines.append(
            "| {case_id} | {zcp_avg_total_tokens:.1f} | {mcp_avg_total_tokens:.1f} | {token_delta_mcp_minus_zcp:.1f} | {mcp_vs_zcp_ratio:.2f}x | {zcp_accuracy:.1%} | {mcp_accuracy:.1%} |".format(
                **row
            )
        )
    return "\n".join(lines)


def json_report(runs: list[RunRecord], *, model: str, repeats: int) -> dict[str, Any]:
    return {
        "model": model,
        "repeats": repeats,
        "summary": [asdict(item) for item in summarize_runs(runs)],
        "cases": case_breakdown(runs),
        "runs": [asdict(item) for item in runs],
    }


def write_reports(
    runs: list[RunRecord],
    *,
    output_dir: str | Path,
    model: str,
    repeats: int,
) -> tuple[Path, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = output_dir / "zcp_mcp_tool_call_benchmark.md"
    json_path = output_dir / "zcp_mcp_tool_call_benchmark.json"
    markdown_path.write_text(markdown_report(runs, model=model, repeats=repeats), encoding="utf-8")
    json_path.write_text(json.dumps(json_report(runs, model=model, repeats=repeats), ensure_ascii=False, indent=2), encoding="utf-8")
    return markdown_path, json_path


def print_summary_table(runs: list[RunRecord]) -> str:
    lines = [
        "| Protocol | Runs | Answer Accuracy | Tool Compliance | Avg Prompt Tokens | Avg Completion Tokens | Avg Total Tokens |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for summary in summarize_runs(runs):
        lines.append(
            "| {protocol} | {runs} | {answer_accuracy:.1%} | {tool_compliance:.1%} | {avg_prompt_tokens:.1f} | {avg_completion_tokens:.1f} | {avg_total_tokens:.1f} |".format(
                **asdict(summary)
            )
        )
    return "\n".join(lines)
