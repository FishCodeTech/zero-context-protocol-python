from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Iterable, Protocol

from tools.benchmarking import (
    DEFAULT_API_KEY,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_REPEATS,
    ToolSpec,
    UsageTotals,
    _build_openai_tools,
    _dump_message,
    _extract_usage,
    _get_value,
    _message_text,
    make_openai_client,
    parse_json_object,
)
from tools.excel_benchmarking import DEFAULT_EXCEL_REPO, MCPRelayServerSession
from tools.excel_benchmark_suites import ExcelBenchmarkCase, tier_a_cases, tier_b_cases, tier_c_cases, tier_d_cases

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = ROOT / "zero-context-protocol-python" / "benchmark_reports"
MAX_TOOL_ROUNDS = 14
PAIRWISE_COMPARISONS = [
    ("original_mcp_zcp_vs_mcp", "zcp_client_to_original_mcp", "mcp_client_to_original_mcp"),
    ("native_zcp_zcp_vs_mcp_surface", "zcp_client_to_native_zcp", "mcp_client_to_zcp_mcp_surface"),
]


@dataclass
class ExcelLLMRunRecord:
    tier: str
    case_id: str
    backend_id: str
    client_kind: str
    server_mode: str
    autonomous: bool
    repeat_index: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    answer_ok: bool
    workbook_ok: bool
    tool_ok: bool
    turns: int
    tool_history: list[str]
    final_text: str
    parsed_output: dict[str, Any] | None
    evaluation_note: str
    required_tool_count: int
    actual_tool_count: int
    extra_tool_calls: int
    duplicate_tool_calls: int
    planning_efficiency: float
    tool_subset_size: int
    error: str | None = None


@dataclass
class ExcelLLMSummary:
    tier: str
    backend_id: str
    client_kind: str
    server_mode: str
    autonomous_case_rate: float
    runs: int
    answer_accuracy: float
    workbook_accuracy: float
    tool_compliance: float
    avg_prompt_tokens: float
    avg_completion_tokens: float
    avg_total_tokens: float
    avg_turns: float
    avg_tool_calls: float
    avg_extra_tool_calls: float
    avg_duplicate_tool_calls: float
    avg_planning_efficiency: float
    avg_tool_subset_size: float


@dataclass(frozen=True)
class ExcelLLMProgressEvent:
    tier: str
    backend_id: str
    client_kind: str
    server_mode: str
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
    workbook_ok: bool | None = None
    tool_ok: bool | None = None
    turns: int | None = None
    error: str | None = None


class ExcelLLMBackend(Protocol):
    backend_id: str
    client_kind: str
    server_mode: str

    async def __aenter__(self) -> "ExcelLLMBackend": ...
    async def __aexit__(self, exc_type, exc, tb) -> None: ...
    async def list_tools(self) -> list[ToolSpec]: ...
    async def call_tool(self, source_name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


def excel_llm_cases(*, tiers: Iterable[str] | None = None, case_limit: int | None = None) -> list[ExcelBenchmarkCase]:
    allowed = {item.upper() for item in tiers} if tiers else {"A", "B", "C", "D"}
    cases: list[ExcelBenchmarkCase] = []
    if "A" in allowed:
        cases.extend(tier_a_cases())
    if "B" in allowed:
        cases.extend(tier_b_cases())
    if "C" in allowed:
        cases.extend(tier_c_cases())
    if "D" in allowed:
        cases.extend(tier_d_cases())
    cases.sort(key=lambda item: (item.tier, item.case_id))
    if case_limit is not None:
        cases = cases[:case_limit]
    return cases


def backend_factories(
    *,
    python_executable: str,
    excel_repo: Path,
) -> list[Callable[[], ExcelLLMBackend]]:
    return [
        lambda: MCPExcelBackend(
            backend_id="mcp_client_to_original_mcp",
            client_kind="mcp_client",
            server_mode="original_mcp",
            python_executable=python_executable,
            excel_repo=excel_repo,
            server_args=["-m", "excel_mcp", "stdio"],
        ),
        lambda: ZCPRelayExcelBackend(
            backend_id="zcp_client_to_original_mcp",
            client_kind="zcp_client",
            server_mode="original_mcp",
            python_executable=python_executable,
            excel_repo=excel_repo,
            server_args=["-m", "excel_mcp", "stdio"],
        ),
        lambda: NativeZCPExcelBackend(),
        lambda: MCPExcelBackend(
            backend_id="mcp_client_to_zcp_mcp_surface",
            client_kind="mcp_client",
            server_mode="native_zcp",
            python_executable=python_executable,
            excel_repo=excel_repo,
            server_args=["-m", "excel_mcp", "zcp-mcp-stdio"],
        ),
    ]


def backend_ids() -> list[str]:
    return [
        "mcp_client_to_original_mcp",
        "zcp_client_to_original_mcp",
        "zcp_client_to_native_zcp",
        "mcp_client_to_zcp_mcp_surface",
    ]


def _system_prompt(case: ExcelBenchmarkCase, backend: ExcelLLMBackend | None = None) -> str:
    base = (
        "你是一个严格依赖工具执行 Excel 操作的助手。"
        " 只要任务要求创建、写入、格式化、读取、检查或修复工作簿，就必须调用工具。"
        " 不允许凭想象编造已经写入或读取到的内容。"
        " 如果多个步骤彼此独立，可以在同一轮并行调用工具。"
        " 最终答案只能输出单行 JSON，不能包含解释、Markdown 或代码块。"
    )
    if backend is not None and backend.backend_id == "zcp_client_to_native_zcp":
        base += (
            " 当前是原生 ZCP 路径：优先把同一块区域的样式变更合并到一次 `format_range`，"
            "把同一张表的连续写入合并到一次 `write_data_to_excel`，避免增量式重复调用。"
            " 在已有工作簿修复场景中，先确认结构，再做最少次数的修改和一次性校验。"
            " 如果提供了高层 workflow tool，默认只调用该 workflow tool；只有它失败时才退回到底层工具。"
        )
    if case.autonomous:
        return base + " 当前任务强调自主规划，不要复述工具名，也不要先问澄清，直接规划并执行。"
    return base + " 当前任务是 benchmark case，请尽量用满足目标的最短工具路径完成。"


def _conversation_start(case: ExcelBenchmarkCase, workdir: Path, backend: ExcelLLMBackend | None = None) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": _system_prompt(case, backend)},
        {"role": "user", "content": case.prompt_factory(workdir)},
    ]


def _required_tool_calls_for_case(case: ExcelBenchmarkCase, backend_id: str) -> dict[str, int]:
    if backend_id == "zcp_client_to_native_zcp" and case.native_zcp_required_tool_calls:
        return case.native_zcp_required_tool_calls
    return case.required_tool_calls


def _native_zcp_initial_subset(
    case: ExcelBenchmarkCase,
    prompt: str,
    tools: list[ToolSpec],
    required_tool_calls: dict[str, int] | None = None,
) -> list[ToolSpec]:
    return _native_zcp_stage_subset(case, prompt, tools, turn_index=1, required_tool_calls=required_tool_calls)


def _native_zcp_candidate_subset(
    case: ExcelBenchmarkCase,
    prompt: str,
    tools: list[ToolSpec],
    required_tool_calls: dict[str, int] | None = None,
) -> list[ToolSpec]:
    if not case.autonomous and not case.native_zcp_required_tool_calls:
        return tools

    text = prompt.lower()
    tools_by_name = {tool.source_name: tool for tool in tools}
    selected: set[str] = set()
    required = required_tool_calls or case.required_tool_calls
    required_names = set(required.keys())
    base_names = {"get_workbook_metadata", "create_worksheet", "write_data_to_excel"}

    def include(*names: str) -> None:
        for name in names:
            if name in tools_by_name:
                selected.add(name)

    semantic_required = [name for name in required_names if name.startswith("zcp_") and name in tools_by_name]
    if semantic_required:
        include(*semantic_required)
        preferred_order = [tool.source_name for tool in tools if tool.source_name in selected]
        return [tools_by_name[name] for name in preferred_order]

    include(*required_names)
    include(*base_names)

    keyword_groups = [
        (
            ("brief", "dashboard", "overview", "title", "board", "packet", "简报", "概览", "标题", "董事会"),
            {"workbook", "worksheet", "layout", "formatting", "readback", "inspection", "write"},
        ),
        (
            ("formula", "revenue", "cost", "profit", "summary", "month", "close", "收入", "成本", "利润", "汇总", "月结"),
            {"workbook", "worksheet", "formula", "validation", "readback", "write"},
        ),
        (
            ("register", "request", "table", "intake", "登记", "需求", "表格"),
            {"workbook", "worksheet", "table", "layout", "readback", "write"},
        ),
        (
            ("snapshot", "copy", "rename", "plan", "team", "members", "快照", "复制", "重命名", "排班", "成员"),
            {"workbook", "worksheet", "copy", "structure", "readback", "write"},
        ),
        (
            ("cleanup", "staging", "delete", "insert", "row", "column", "清理", "删除", "插入", "行", "列"),
            {"worksheet", "structure", "rows", "columns", "readback", "inspection"},
        ),
    ]

    matched_groups: set[str] = set()
    for keywords, groups in keyword_groups:
        if any(keyword in text for keyword in keywords):
            matched_groups.update(groups)
    for tool in tools:
        if tool.source_name in required_names:
            metadata = tool.metadata or {}
            matched_groups.update(str(group) for group in metadata.get("groups", []) if isinstance(group, str))
    if not matched_groups:
        matched_groups.update({"workbook", "worksheet", "write", "layout", "readback"})

    for tool in tools:
        metadata = tool.metadata or {}
        groups = set(metadata.get("groups") or [])
        if groups & matched_groups:
            selected.add(tool.source_name)

    # Avoid early noise from analytics and deletion tools unless the prompt clearly asks for them.
    if "pivot" not in text and "图表" not in text and "chart" not in text:
        selected.discard("create_chart")
        selected.discard("create_pivot_table")
    if not any(keyword in text for keyword in ("formula", "profit", "revenue", "cost", "summary", "收入", "成本", "利润", "汇总", "月结")):
        if "apply_formula" not in required_names:
            selected.discard("apply_formula")
        if "validate_formula_syntax" not in required_names:
            selected.discard("validate_formula_syntax")
    if "delete worksheet" not in text and "删除工作表" not in text:
        selected.discard("delete_worksheet")

    if any(keyword in text for keyword in ("already", "existing", "draft", "已", "草稿", "已有")):
        selected.discard("create_workbook")
    preferred_order = [tool.source_name for tool in tools if tool.source_name in selected]
    return [tools_by_name[name] for name in preferred_order]


def _native_zcp_stage_subset(
    case: ExcelBenchmarkCase,
    prompt: str,
    tools: list[ToolSpec],
    turn_index: int,
    required_tool_calls: dict[str, int] | None = None,
) -> list[ToolSpec]:
    candidates = _native_zcp_candidate_subset(case, prompt, tools, required_tool_calls=required_tool_calls)
    if (not case.autonomous and not case.native_zcp_required_tool_calls) or not candidates:
        return tools

    if turn_index <= 2:
        allowed_stages = {"setup", "operate", "repair"}
    elif turn_index <= 5:
        allowed_stages = {"setup", "operate", "repair", "polish", "calculate"}
    elif turn_index <= 10:
        allowed_stages = {"setup", "operate", "repair", "polish", "calculate", "verify"}
    else:
        return tools

    staged: list[ToolSpec] = []
    for tool in candidates:
        metadata = tool.metadata or {}
        stages = {str(item) for item in (metadata.get("stages") or [])}
        if not stages or stages & allowed_stages:
            staged.append(tool)
    return staged or candidates


def _tool_specs_for_turn(
    *,
    backend: ExcelLLMBackend,
    case: ExcelBenchmarkCase,
    prompt: str,
    all_tool_specs: list[ToolSpec],
    turn_index: int,
    tool_history: list[str] | None = None,
) -> list[ToolSpec]:
    if backend.backend_id != "zcp_client_to_native_zcp":
        return all_tool_specs
    required_tool_calls = _required_tool_calls_for_case(case, backend.backend_id)
    subset = _native_zcp_stage_subset(
        case,
        prompt,
        all_tool_specs,
        turn_index,
        required_tool_calls=required_tool_calls,
    )
    if len(subset) >= len(all_tool_specs):
        return all_tool_specs

    # If the model is looping on the same small set of tools, widen immediately.
    if _has_tool_loop(tool_history or []):
        return all_tool_specs
    return subset


def _has_tool_loop(tool_history: list[str]) -> bool:
    if len(tool_history) < 6:
        return False
    recent = tool_history[-6:]
    return len(set(recent)) <= 2


async def _run_case_with_backend(
    backend: ExcelLLMBackend,
    *,
    client: Any,
    model: str,
    case: ExcelBenchmarkCase,
    repeat_index: int,
    temperature: float,
) -> ExcelLLMRunRecord:
    usage = UsageTotals()
    tool_history: list[str] = []
    tool_specs = await backend.list_tools()
    tool_subset_size = len(tool_specs)
    tools, exposed_to_source = _build_openai_tools(tool_specs)

    with TemporaryDirectory(prefix=f"{backend.backend_id}-{case.case_id}-") as temp_dir:
        workdir = Path(temp_dir)
        messages = _conversation_start(case, workdir, backend)
        prompt = str(messages[-1]["content"])

        for turn_index in range(1, MAX_TOOL_ROUNDS + 1):
            active_tool_specs = _tool_specs_for_turn(
                backend=backend,
                case=case,
                prompt=prompt,
                all_tool_specs=tool_specs,
                turn_index=turn_index,
                tool_history=tool_history,
            )
            if turn_index == 1:
                tool_subset_size = len(active_tool_specs)
            tools, exposed_to_source = _build_openai_tools(active_tool_specs)
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
                return _build_run_record(
                    backend=backend,
                    case=case,
                    repeat_index=repeat_index,
                    usage=usage,
                    turns=turn_index,
                    tool_history=tool_history,
                    final_text="",
                    parsed_output=None,
                    answer_ok=False,
                    workbook_ok=False,
                    evaluation_note="empty_choices",
                    tool_subset_size=tool_subset_size,
                    error="empty_choices",
                )

            message = _get_value(choices[0], "message", {})
            tool_calls = _get_value(message, "tool_calls", []) or []
            if not tool_calls:
                final_text = _message_text(message)
                parsed = parse_json_object(final_text)
                answer_ok, workbook_ok, note = case.evaluator(parsed, workdir)
                return _build_run_record(
                    backend=backend,
                    case=case,
                    repeat_index=repeat_index,
                    usage=usage,
                    turns=turn_index,
                    tool_history=tool_history,
                    final_text=final_text,
                    parsed_output=parsed,
                    answer_ok=answer_ok,
                    workbook_ok=workbook_ok,
                    evaluation_note=note,
                    tool_subset_size=tool_subset_size,
                )

            messages.append(_dump_message(message))
            for tool_call in tool_calls:
                function = _get_value(tool_call, "function", {})
                exposed_name = str(_get_value(function, "name", ""))
                if exposed_name not in exposed_to_source:
                    payload = {"isError": True, "error": f"unknown_tool:{exposed_name}"}
                    source_name = exposed_name
                else:
                    source_name = exposed_to_source[exposed_name]
                    arguments = parse_tool_arguments(_get_value(function, "arguments", "{}"))
                    if "__parse_error__" in arguments:
                        payload = {
                            "isError": True,
                            "error": f"invalid_tool_arguments:{source_name}",
                            "rawArguments": arguments["__parse_error__"],
                        }
                    else:
                        try:
                            payload = await backend.call_tool(source_name, arguments)
                        except Exception as exc:
                            payload = {"isError": True, "error": str(exc)}
                tool_history.append(source_name)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": _get_value(tool_call, "id"),
                        "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    }
                )

        answer_ok, workbook_ok, note = case.evaluator(None, workdir)
        return _build_run_record(
            backend=backend,
            case=case,
            repeat_index=repeat_index,
            usage=usage,
            turns=MAX_TOOL_ROUNDS,
            tool_history=tool_history,
            final_text="",
            parsed_output=None,
            answer_ok=answer_ok,
            workbook_ok=workbook_ok,
            evaluation_note=note,
            tool_subset_size=tool_subset_size,
            error="max_tool_rounds_exceeded",
        )


async def run_excel_llm_benchmark_async(
    *,
    api_key: str = DEFAULT_API_KEY,
    base_url: str = DEFAULT_BASE_URL,
    model: str = DEFAULT_MODEL,
    repeats: int = DEFAULT_REPEATS,
    temperature: float = 0.0,
    python_executable: str,
    excel_repo: Path = DEFAULT_EXCEL_REPO,
    tiers: Iterable[str] | None = None,
    case_limit: int | None = None,
    backends: Iterable[str] | None = None,
    checkpoint_path: str | Path | None = None,
    progress: Callable[[ExcelLLMProgressEvent], None] | None = None,
) -> list[ExcelLLMRunRecord]:
    client = make_openai_client(api_key=api_key, base_url=base_url)
    cases = excel_llm_cases(tiers=tiers, case_limit=case_limit)
    selected_backends = set(backends or backend_ids())
    checkpoint = Path(checkpoint_path) if checkpoint_path else None
    runs = load_checkpoint_records(checkpoint) if checkpoint and checkpoint.exists() else []
    completed = {
        (run.backend_id, run.repeat_index, run.tier, run.case_id)
        for run in runs
    }
    overall_total = repeats * len(cases) * len(selected_backends)
    overall_index = 0

    for factory in backend_factories(python_executable=python_executable, excel_repo=excel_repo):
        backend = factory()
        if backend.backend_id not in selected_backends:
            continue
        async with backend:
            for repeat_index in range(1, repeats + 1):
                for case_index, case in enumerate(cases, start=1):
                    key = (backend.backend_id, repeat_index, case.tier, case.case_id)
                    if key in completed:
                        overall_index += 1
                        continue
                    overall_index += 1
                    if progress is not None:
                        progress(
                            ExcelLLMProgressEvent(
                                tier=case.tier,
                                backend_id=backend.backend_id,
                                client_kind=backend.client_kind,
                                server_mode=backend.server_mode,
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
                        repeat_index=repeat_index,
                        temperature=temperature,
                    )
                    runs.append(record)
                    if checkpoint is not None:
                        append_checkpoint_record(checkpoint, record)
                    if progress is not None:
                        progress(
                            ExcelLLMProgressEvent(
                                tier=case.tier,
                                backend_id=backend.backend_id,
                                client_kind=backend.client_kind,
                                server_mode=backend.server_mode,
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
                                workbook_ok=record.workbook_ok,
                                tool_ok=record.tool_ok,
                                turns=record.turns,
                                error=record.error,
                            )
                        )
    return runs


def run_excel_llm_benchmark(
    *,
    api_key: str = DEFAULT_API_KEY,
    base_url: str = DEFAULT_BASE_URL,
    model: str = DEFAULT_MODEL,
    repeats: int = DEFAULT_REPEATS,
    temperature: float = 0.0,
    python_executable: str,
    excel_repo: Path = DEFAULT_EXCEL_REPO,
    tiers: Iterable[str] | None = None,
    case_limit: int | None = None,
    backends: Iterable[str] | None = None,
    checkpoint_path: str | Path | None = None,
    progress: Callable[[ExcelLLMProgressEvent], None] | None = None,
) -> list[ExcelLLMRunRecord]:
    return asyncio.run(
        run_excel_llm_benchmark_async(
            api_key=api_key,
            base_url=base_url,
            model=model,
            repeats=repeats,
            temperature=temperature,
            python_executable=python_executable,
            excel_repo=excel_repo,
            tiers=tiers,
            case_limit=case_limit,
            backends=backends,
            checkpoint_path=checkpoint_path,
            progress=progress,
        )
    )


def summarize_runs(runs: list[ExcelLLMRunRecord], *, by_tier: bool = False) -> list[ExcelLLMSummary]:
    grouped: dict[tuple[str, str], list[ExcelLLMRunRecord]] = {}
    for run in runs:
        tier_key = run.tier if by_tier else "ALL"
        grouped.setdefault((tier_key, run.backend_id), []).append(run)
    summaries: list[ExcelLLMSummary] = []
    for (tier, backend_id), items in sorted(grouped.items()):
        count = len(items)
        first = items[0]
        summaries.append(
            ExcelLLMSummary(
                tier=tier,
                backend_id=backend_id,
                client_kind=first.client_kind,
                server_mode=first.server_mode,
                autonomous_case_rate=sum(1 for item in items if item.autonomous) / count,
                runs=count,
                answer_accuracy=sum(1 for item in items if item.answer_ok) / count,
                workbook_accuracy=sum(1 for item in items if item.workbook_ok) / count,
                tool_compliance=sum(1 for item in items if item.tool_ok) / count,
                avg_prompt_tokens=sum(item.prompt_tokens for item in items) / count,
                avg_completion_tokens=sum(item.completion_tokens for item in items) / count,
                avg_total_tokens=sum(item.total_tokens for item in items) / count,
                avg_turns=sum(item.turns for item in items) / count,
                avg_tool_calls=sum(item.actual_tool_count for item in items) / count,
                avg_extra_tool_calls=sum(item.extra_tool_calls for item in items) / count,
                avg_duplicate_tool_calls=sum(item.duplicate_tool_calls for item in items) / count,
                avg_planning_efficiency=sum(item.planning_efficiency for item in items) / count,
                avg_tool_subset_size=sum(item.tool_subset_size for item in items) / count,
            )
        )
    return summaries


def case_breakdown(runs: list[ExcelLLMRunRecord]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[ExcelLLMRunRecord]] = {}
    backend_order = backend_ids()
    cases = excel_llm_cases()
    for run in runs:
        grouped.setdefault((run.case_id, run.backend_id), []).append(run)

    rows: list[dict[str, Any]] = []
    for case in cases:
        row: dict[str, Any] = {
            "tier": case.tier,
            "case_id": case.case_id,
            "autonomous": case.autonomous,
            "required_tool_count": sum(case.required_tool_calls.values()),
        }
        for backend_id in backend_order:
            items = grouped.get((case.case_id, backend_id), [])
            prefix = backend_id
            if items:
                row[f"{prefix}_avg_total_tokens"] = sum(item.total_tokens for item in items) / len(items)
                row[f"{prefix}_answer_accuracy"] = sum(1 for item in items if item.answer_ok) / len(items)
                row[f"{prefix}_workbook_accuracy"] = sum(1 for item in items if item.workbook_ok) / len(items)
                row[f"{prefix}_tool_compliance"] = sum(1 for item in items if item.tool_ok) / len(items)
                row[f"{prefix}_avg_turns"] = sum(item.turns for item in items) / len(items)
                row[f"{prefix}_avg_tool_calls"] = sum(item.actual_tool_count for item in items) / len(items)
                row[f"{prefix}_avg_extra_tool_calls"] = sum(item.extra_tool_calls for item in items) / len(items)
                row[f"{prefix}_avg_planning_efficiency"] = sum(item.planning_efficiency for item in items) / len(items)
            else:
                for suffix in (
                    "avg_total_tokens",
                    "answer_accuracy",
                    "workbook_accuracy",
                    "tool_compliance",
                    "avg_turns",
                    "avg_tool_calls",
                    "avg_extra_tool_calls",
                    "avg_planning_efficiency",
                ):
                    row[f"{prefix}_{suffix}"] = 0.0
        for label, left_id, right_id in PAIRWISE_COMPARISONS:
            _add_pairwise_case_metrics(row, left_id=left_id, right_id=right_id, label=label)
        rows.append(row)
    return [row for row in rows if any(run.case_id == row["case_id"] for run in runs)]


def markdown_report(runs: list[ExcelLLMRunRecord], *, model: str, repeats: int) -> str:
    overall = summarize_runs(runs)
    tiered = summarize_runs(runs, by_tier=True)
    cases = case_breakdown(runs)
    lines = [
        "# Excel LLM Token Benchmark",
        "",
        f"- model: `{model}`",
        f"- repeats: `{repeats}`",
        f"- total cases: `{len({run.case_id for run in runs})}`",
        "",
        "## Overall Summary",
        "",
        "| Backend | Client | Server Mode | Runs | Answer | Workbook | Tool | Avg Prompt | Avg Completion | Avg Total | Avg Turns | Avg Tool Calls | Avg Extra Calls | Planning Eff. |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for summary in overall:
        lines.append(
            "| {backend_id} | {client_kind} | {server_mode} | {runs} | {answer_accuracy:.1%} | {workbook_accuracy:.1%} | {tool_compliance:.1%} | {avg_prompt_tokens:.1f} | {avg_completion_tokens:.1f} | {avg_total_tokens:.1f} | {avg_turns:.1f} | {avg_tool_calls:.1f} | {avg_extra_tool_calls:.1f} | {avg_planning_efficiency:.2f} |".format(
                **asdict(summary)
            )
        )

    lines.extend(
        [
            "",
            "## Tier Summary",
            "",
            "| Tier | Backend | Runs | Answer | Workbook | Tool | Avg Total | Avg Turns | Avg Tool Calls | Avg Extra Calls | Planning Eff. | Autonomous Rate |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for summary in tiered:
        lines.append(
            "| {tier} | {backend_id} | {runs} | {answer_accuracy:.1%} | {workbook_accuracy:.1%} | {tool_compliance:.1%} | {avg_total_tokens:.1f} | {avg_turns:.1f} | {avg_tool_calls:.1f} | {avg_extra_tool_calls:.1f} | {avg_planning_efficiency:.2f} | {autonomous_case_rate:.1%} |".format(
                **asdict(summary)
            )
        )

    lines.extend(
        [
            "",
            "## Pairwise Comparison",
            "",
            "| Scope | Comparison | Left Avg Total | Right Avg Total | Token Delta | Ratio |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in _pairwise_summary(overall):
        lines.append(
            "| overall | {label} | {left_avg_total_tokens:.1f} | {right_avg_total_tokens:.1f} | {right_minus_left:.1f} | {right_div_left:.2f}x |".format(
                **row
            )
        )
    for tier_name in sorted({item.tier for item in tiered}):
        tier_rows = [item for item in tiered if item.tier == tier_name]
        for row in _pairwise_summary(tier_rows):
            lines.append(
                "| {tier_name} | {label} | {left_avg_total_tokens:.1f} | {right_avg_total_tokens:.1f} | {right_minus_left:.1f} | {right_div_left:.2f}x |".format(
                    tier_name=tier_name,
                    **row,
                )
            )

    lines.extend(
        [
            "",
            "## Case Breakdown",
            "",
            "| Tier | Case | ZCP->Original MCP | MCP->Original MCP | ZCP->Native ZCP | MCP->ZCP MCP Surface |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in cases:
        lines.append(
            "| {tier} | {case_id} | {zcp_client_to_original_mcp_avg_total_tokens:.1f} | {mcp_client_to_original_mcp_avg_total_tokens:.1f} | {zcp_client_to_native_zcp_avg_total_tokens:.1f} | {mcp_client_to_zcp_mcp_surface_avg_total_tokens:.1f} |".format(
                **row
            )
        )
    return "\n".join(lines)


def json_report(runs: list[ExcelLLMRunRecord], *, model: str, repeats: int) -> dict[str, Any]:
    overall = summarize_runs(runs)
    tiered = summarize_runs(runs, by_tier=True)
    return {
        "model": model,
        "repeats": repeats,
        "overall_summary": [asdict(item) for item in overall],
        "tier_summary": [asdict(item) for item in tiered],
        "overall_pairwise": _pairwise_summary(overall),
        "tier_pairwise": {
            tier: _pairwise_summary([item for item in tiered if item.tier == tier])
            for tier in sorted({item.tier for item in tiered})
        },
        "cases": case_breakdown(runs),
        "runs": [asdict(item) for item in runs],
    }


def write_reports(
    runs: list[ExcelLLMRunRecord],
    *,
    output_dir: str | Path,
    model: str,
    repeats: int,
) -> tuple[Path, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = output_dir / "excel_llm_token_benchmark.md"
    json_path = output_dir / "excel_llm_token_benchmark.json"
    markdown_path.write_text(markdown_report(runs, model=model, repeats=repeats), encoding="utf-8")
    json_path.write_text(json.dumps(json_report(runs, model=model, repeats=repeats), ensure_ascii=False, indent=2), encoding="utf-8")
    return markdown_path, json_path


def append_checkpoint_record(path: Path, record: ExcelLLMRunRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")


def load_checkpoint_records(path: Path | None) -> list[ExcelLLMRunRecord]:
    if path is None or not path.exists():
        return []
    records: list[ExcelLLMRunRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        payload = json.loads(line)
        records.append(ExcelLLMRunRecord(**payload))
    return records


def print_summary_table(runs: list[ExcelLLMRunRecord]) -> str:
    lines = [
        "| Backend | Runs | Answer | Workbook | Tool | Avg Total | Avg Turns | Avg Tool Calls | Avg Extra Calls | Planning Eff. |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for summary in summarize_runs(runs):
        lines.append(
            "| {backend_id} | {runs} | {answer_accuracy:.1%} | {workbook_accuracy:.1%} | {tool_compliance:.1%} | {avg_total_tokens:.1f} | {avg_turns:.1f} | {avg_tool_calls:.1f} | {avg_extra_tool_calls:.1f} | {avg_planning_efficiency:.2f} |".format(
                **asdict(summary)
            )
        )
    return "\n".join(lines)


def evaluate_tool_history(case: ExcelBenchmarkCase, tool_history: Iterable[str], *, backend_id: str) -> bool:
    required_tool_calls = _required_tool_calls_for_case(case, backend_id)
    counts: dict[str, int] = {}
    for item in tool_history:
        counts[item] = counts.get(item, 0) + 1
    return all(counts.get(kind, 0) >= expected_count for kind, expected_count in required_tool_calls.items())


class MCPExcelBackend:
    def __init__(
        self,
        *,
        backend_id: str,
        client_kind: str,
        server_mode: str,
        python_executable: str,
        excel_repo: Path,
        server_args: list[str],
    ) -> None:
        self.backend_id = backend_id
        self.client_kind = client_kind
        self.server_mode = server_mode
        self.python_executable = python_executable
        self.excel_repo = excel_repo
        self.server_args = server_args
        self._stdio_cm = None
        self._session_cm = None
        self._session = None

    async def __aenter__(self) -> "MCPExcelBackend":
        from mcp.client.session import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        self._stdio_cm = stdio_client(
            StdioServerParameters(command=self.python_executable, args=self.server_args, cwd=str(self.excel_repo))
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
                exposed_name=tool.name,
                description=tool.description or tool.title or tool.name,
                input_schema=tool.inputSchema,
                metadata=None,
            )
            for tool in result.tools
        ]

    async def call_tool(self, source_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self._session.call_tool(source_name, arguments)
        return result.model_dump(exclude_none=True, by_alias=True)


class ZCPRelayExcelBackend:
    def __init__(
        self,
        *,
        backend_id: str,
        client_kind: str,
        server_mode: str,
        python_executable: str,
        excel_repo: Path,
        server_args: list[str],
    ) -> None:
        self.backend_id = backend_id
        self.client_kind = client_kind
        self.server_mode = server_mode
        self.python_executable = python_executable
        self.excel_repo = excel_repo
        self.server_args = server_args
        self._stdio_cm = None
        self._session_cm = None
        self._mcp_session = None
        self._zcp_client = None

    async def __aenter__(self) -> "ZCPRelayExcelBackend":
        from mcp.client.session import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client as mcp_stdio_client
        from zcp import ZCPClientSession

        self._stdio_cm = mcp_stdio_client(
            StdioServerParameters(command=self.python_executable, args=self.server_args, cwd=str(self.excel_repo))
        )
        read_stream, write_stream = await self._stdio_cm.__aenter__()
        self._session_cm = ClientSession(read_stream, write_stream)
        self._mcp_session = await self._session_cm.__aenter__()
        await self._mcp_session.initialize()
        relay = MCPRelayServerSession(self._mcp_session)
        self._zcp_client = ZCPClientSession(relay, transport="mcp-relay")
        await self._zcp_client.initialize()
        await self._zcp_client.initialized()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._session_cm is not None:
            await self._session_cm.__aexit__(exc_type, exc, tb)
        if self._stdio_cm is not None:
            await self._stdio_cm.__aexit__(exc_type, exc, tb)

    async def list_tools(self) -> list[ToolSpec]:
        result = await self._zcp_client.list_tools()
        return [
            ToolSpec(
                source_name=tool["name"],
                exposed_name=tool["name"],
                description=tool.get("description") or tool["name"],
                input_schema=tool["inputSchema"],
                metadata=tool.get("_meta"),
            )
            for tool in result["tools"]
        ]

    async def call_tool(self, source_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._zcp_client.call_tool(source_name, arguments)


class NativeZCPExcelBackend:
    backend_id = "zcp_client_to_native_zcp"
    client_kind = "zcp_client"
    server_mode = "native_zcp"

    def __init__(self) -> None:
        self._client = None

    async def __aenter__(self) -> "NativeZCPExcelBackend":
        from excel_mcp.zcp_server import build_excel_zcp_app
        from zcp import stdio_client, stdio_server

        app = build_excel_zcp_app()
        self._client = stdio_client(stdio_server(app))
        await self._client.initialize()
        await self._client.initialized()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def list_tools(self) -> list[ToolSpec]:
        result = await self._client.list_tools(profile="semantic-workflow")
        return [
            ToolSpec(
                source_name=tool["name"],
                exposed_name=tool["name"],
                description=tool.get("description") or tool["name"],
                input_schema=tool["inputSchema"],
                metadata=tool.get("_meta"),
            )
            for tool in result["tools"]
        ]

    async def call_tool(self, source_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._client.call_tool(source_name, arguments)


def _build_run_record(
    *,
    backend: ExcelLLMBackend,
    case: ExcelBenchmarkCase,
    repeat_index: int,
    usage: UsageTotals,
    turns: int,
    tool_history: list[str],
    final_text: str,
    parsed_output: dict[str, Any] | None,
    answer_ok: bool,
    workbook_ok: bool,
    evaluation_note: str,
    tool_subset_size: int,
    error: str | None = None,
) -> ExcelLLMRunRecord:
    required_tool_calls = _required_tool_calls_for_case(case, backend.backend_id)
    required_tool_count = sum(required_tool_calls.values())
    actual_tool_count = len(tool_history)
    duplicate_tool_calls = _duplicate_tool_calls(tool_history)
    extra_tool_calls = _extra_tool_calls(required_tool_calls, tool_history)
    planning_efficiency = (required_tool_count / actual_tool_count) if actual_tool_count else 0.0
    return ExcelLLMRunRecord(
        tier=case.tier,
        case_id=case.case_id,
        backend_id=backend.backend_id,
        client_kind=backend.client_kind,
        server_mode=backend.server_mode,
        autonomous=case.autonomous,
        repeat_index=repeat_index,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        answer_ok=answer_ok,
        workbook_ok=workbook_ok,
        tool_ok=evaluate_tool_history(case, tool_history, backend_id=backend.backend_id),
        turns=turns,
        tool_history=list(tool_history),
        final_text=final_text,
        parsed_output=parsed_output,
        evaluation_note=evaluation_note,
        required_tool_count=required_tool_count,
        actual_tool_count=actual_tool_count,
        extra_tool_calls=extra_tool_calls,
        duplicate_tool_calls=duplicate_tool_calls,
        planning_efficiency=planning_efficiency,
        tool_subset_size=tool_subset_size,
        error=error,
    )


def _duplicate_tool_calls(tool_history: Iterable[str]) -> int:
    counts: dict[str, int] = {}
    for item in tool_history:
        counts[item] = counts.get(item, 0) + 1
    return sum(max(0, count - 1) for count in counts.values())


def _extra_tool_calls(required: dict[str, int], tool_history: Iterable[str]) -> int:
    counts: dict[str, int] = {}
    for item in tool_history:
        counts[item] = counts.get(item, 0) + 1
    extra = 0
    for tool_name, count in counts.items():
        extra += max(0, count - required.get(tool_name, 0))
    return extra


def _pairwise_summary(summaries: list[ExcelLLMSummary]) -> list[dict[str, Any]]:
    by_backend = {summary.backend_id: summary for summary in summaries}
    rows: list[dict[str, Any]] = []
    for label, left_id, right_id in PAIRWISE_COMPARISONS:
        left = by_backend.get(left_id)
        right = by_backend.get(right_id)
        if left is None or right is None:
            continue
        rows.append(
            {
                "label": label,
                "left_id": left_id,
                "right_id": right_id,
                "left_avg_total_tokens": left.avg_total_tokens,
                "right_avg_total_tokens": right.avg_total_tokens,
                "right_minus_left": right.avg_total_tokens - left.avg_total_tokens,
                "right_div_left": (right.avg_total_tokens / left.avg_total_tokens) if left.avg_total_tokens else 0.0,
            }
        )
    return rows


def _add_pairwise_case_metrics(row: dict[str, Any], *, left_id: str, right_id: str, label: str) -> None:
    left_tokens = row.get(f"{left_id}_avg_total_tokens", 0.0)
    right_tokens = row.get(f"{right_id}_avg_total_tokens", 0.0)
    row[f"{label}_delta"] = right_tokens - left_tokens
    row[f"{label}_ratio"] = (right_tokens / left_tokens) if left_tokens else 0.0


def parse_tool_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    raw = str(value or "{}").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {"__parse_error__": raw}
