from types import SimpleNamespace

from tools.benchmarking import ToolSpec
from tools.excel_benchmark_suites import tier_a_cases, tier_b_cases, tier_c_cases, tier_d_cases
from tools.excel_benchmark_suites import tier_d_autonomous_planning as tier_d_module
from tools.excel_llm_benchmarking import (
    ExcelLLMRunRecord,
    _native_zcp_initial_subset,
    _tool_specs_for_turn,
    case_breakdown,
    evaluate_tool_history,
    excel_llm_cases,
    load_checkpoint_records,
    markdown_report,
    parse_tool_arguments,
    summarize_runs,
)


def test_case_loaders_cover_all_tiers() -> None:
    assert len(tier_a_cases()) == 16
    assert len(tier_b_cases()) == 8
    assert len(tier_c_cases()) == 7
    assert len(tier_d_cases()) == 6
    assert len(excel_llm_cases()) == 37
    assert len(excel_llm_cases(tiers=["A"])) == 16
    assert len(excel_llm_cases(tiers=["D"])) == 6


def test_tier_c_cases_define_native_semantic_requirements() -> None:
    cases = tier_c_cases()
    assert len(cases) == 7
    for case in cases:
        assert case.native_zcp_required_tool_calls is not None
        assert sum(case.native_zcp_required_tool_calls.values()) == 1
        name = next(iter(case.native_zcp_required_tool_calls))
        assert name.startswith("zcp_")


def test_tier_b_cases_define_native_semantic_requirements() -> None:
    cases = tier_b_cases()
    assert len(cases) == 8
    for case in cases:
        assert case.native_zcp_required_tool_calls is not None
        assert sum(case.native_zcp_required_tool_calls.values()) == 1
        name = next(iter(case.native_zcp_required_tool_calls))
        assert name.startswith("zcp_")


def test_summary_and_markdown_report_include_tiers_and_backends() -> None:
    runs = [
        ExcelLLMRunRecord(
            tier="A",
            case_id="tier_a_create_workbook",
            backend_id="mcp_client_to_original_mcp",
            client_kind="mcp_client",
            server_mode="original_mcp",
            autonomous=False,
            repeat_index=1,
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
            answer_ok=True,
            workbook_ok=True,
            tool_ok=True,
            turns=2,
            tool_history=["create_workbook"],
            final_text='{"ok":true}',
            parsed_output={"ok": True},
            evaluation_note="ok",
            required_tool_count=1,
            actual_tool_count=1,
            extra_tool_calls=0,
            duplicate_tool_calls=0,
            planning_efficiency=1.0,
            tool_subset_size=25,
        ),
        ExcelLLMRunRecord(
            tier="A",
            case_id="tier_a_create_workbook",
            backend_id="zcp_client_to_original_mcp",
            client_kind="zcp_client",
            server_mode="original_mcp",
            autonomous=False,
            repeat_index=1,
            prompt_tokens=90,
            completion_tokens=18,
            total_tokens=108,
            answer_ok=True,
            workbook_ok=True,
            tool_ok=True,
            turns=2,
            tool_history=["create_workbook"],
            final_text='{"ok":true}',
            parsed_output={"ok": True},
            evaluation_note="ok",
            required_tool_count=1,
            actual_tool_count=1,
            extra_tool_calls=0,
            duplicate_tool_calls=0,
            planning_efficiency=1.0,
            tool_subset_size=25,
        ),
        ExcelLLMRunRecord(
            tier="D",
            case_id="executive_briefing_goal",
            backend_id="zcp_client_to_native_zcp",
            client_kind="zcp_client",
            server_mode="native_zcp",
            autonomous=True,
            repeat_index=1,
            prompt_tokens=80,
            completion_tokens=15,
            total_tokens=95,
            answer_ok=True,
            workbook_ok=True,
            tool_ok=True,
            turns=3,
            tool_history=["create_workbook", "create_worksheet", "write_data_to_excel", "merge_cells"],
            final_text='{"ok":true}',
            parsed_output={"ok": True},
            evaluation_note="ok",
            required_tool_count=4,
            actual_tool_count=4,
            extra_tool_calls=0,
            duplicate_tool_calls=0,
            planning_efficiency=1.0,
            tool_subset_size=25,
        ),
        ExcelLLMRunRecord(
            tier="D",
            case_id="executive_briefing_goal",
            backend_id="mcp_client_to_zcp_mcp_surface",
            client_kind="mcp_client",
            server_mode="native_zcp",
            autonomous=True,
            repeat_index=1,
            prompt_tokens=70,
            completion_tokens=15,
            total_tokens=85,
            answer_ok=True,
            workbook_ok=True,
            tool_ok=True,
            turns=3,
            tool_history=["create_workbook", "create_worksheet", "write_data_to_excel", "merge_cells"],
            final_text='{"ok":true}',
            parsed_output={"ok": True},
            evaluation_note="ok",
            required_tool_count=4,
            actual_tool_count=4,
            extra_tool_calls=0,
            duplicate_tool_calls=0,
            planning_efficiency=1.0,
            tool_subset_size=25,
        ),
    ]

    summaries = summarize_runs(runs)
    assert len(summaries) == 4
    tiered = summarize_runs(runs, by_tier=True)
    assert {item.tier for item in tiered} == {"A", "D"}

    breakdown = case_breakdown(runs)
    first = next(item for item in breakdown if item["case_id"] == "tier_a_create_workbook")
    assert first["mcp_client_to_original_mcp_avg_total_tokens"] == 120
    assert first["zcp_client_to_original_mcp_avg_total_tokens"] == 108

    report = markdown_report(runs, model="deepseek-chat", repeats=1)
    assert "Tier Summary" in report
    assert "Pairwise Comparison" in report
    assert "zcp_client_to_native_zcp" in report
    subset_report = markdown_report([run for run in runs if run.backend_id == "zcp_client_to_native_zcp"], model="deepseek-chat", repeats=1)
    assert "Case Breakdown" in subset_report


def test_parse_tool_arguments_tolerates_bad_json() -> None:
    assert parse_tool_arguments('{"filepath":"/tmp/a.xlsx"}') == {"filepath": "/tmp/a.xlsx"}
    bad = parse_tool_arguments('{"filepath":"/tmp/a.xlsx"')
    assert "__parse_error__" in bad


def test_load_checkpoint_records_round_trips(tmp_path) -> None:
    path = tmp_path / "checkpoint.jsonl"
    path.write_text(
        '{"tier":"A","case_id":"tier_a_create_workbook","backend_id":"mcp_client_to_original_mcp","client_kind":"mcp_client","server_mode":"original_mcp","autonomous":false,"repeat_index":1,"prompt_tokens":1,"completion_tokens":2,"total_tokens":3,"answer_ok":true,"workbook_ok":true,"tool_ok":true,"turns":1,"tool_history":["create_workbook"],"final_text":"{}","parsed_output":{},"evaluation_note":"ok","required_tool_count":1,"actual_tool_count":1,"extra_tool_calls":0,"duplicate_tool_calls":0,"planning_efficiency":1.0,"tool_subset_size":25,"error":null}\n',
        encoding="utf-8",
    )
    records = load_checkpoint_records(path)
    assert len(records) == 1
    assert records[0].backend_id == "mcp_client_to_original_mcp"


def test_tier_d_evaluator_handles_missing_sheet_gracefully(tmp_path) -> None:
    workbook = tmp_path / "board_packet_repair.xlsx"
    tier_d_module._seed_board_packet_draft(workbook)
    answer_ok, workbook_ok, note = tier_d_module._evaluate_board_packet_repair(None, tmp_path)
    assert answer_ok is False
    assert workbook_ok is False
    assert "missing_sheet:Overview" in note


def test_native_zcp_initial_subset_prefers_relevant_layout_tools() -> None:
    case = next(item for item in tier_d_cases() if item.case_id == "board_packet_repair_goal")
    tools = [
        ToolSpec("create_workbook", "create_workbook", "create", {}, {"groups": ["workbook", "create"]}),
        ToolSpec("create_worksheet", "create_worksheet", "sheet", {}, {"groups": ["worksheet", "create"]}),
        ToolSpec("rename_worksheet", "rename_worksheet", "rename", {}, {"groups": ["worksheet", "structure"]}),
        ToolSpec("write_data_to_excel", "write_data_to_excel", "write", {}, {"groups": ["write", "worksheet"]}),
        ToolSpec("merge_cells", "merge_cells", "merge", {}, {"groups": ["layout", "formatting"]}),
        ToolSpec("format_range", "format_range", "format", {}, {"groups": ["layout", "formatting"]}),
        ToolSpec("get_workbook_metadata", "get_workbook_metadata", "meta", {}, {"groups": ["workbook", "inspection"]}),
        ToolSpec("read_data_from_excel", "read_data_from_excel", "read", {}, {"groups": ["readback", "inspection"]}),
        ToolSpec("apply_formula", "apply_formula", "formula", {}, {"groups": ["formula", "write"]}),
        ToolSpec("create_chart", "create_chart", "chart", {}, {"groups": ["chart", "analytics"]}),
    ]
    subset = _native_zcp_initial_subset(case, "整理董事会概览页并修复标题区，补一个细节页", tools)
    names = {tool.source_name for tool in subset}
    assert "rename_worksheet" in names
    assert "merge_cells" in names
    assert "format_range" in names
    assert "get_workbook_metadata" in names
    assert "read_data_from_excel" in names
    assert "create_chart" not in names
    assert "apply_formula" not in names


def test_tool_specs_for_turn_expands_native_zcp_after_routed_window() -> None:
    case = next(item for item in tier_d_cases() if item.case_id == "board_packet_repair_goal")
    tools = [
        ToolSpec("merge_cells", "merge_cells", "merge", {}, {"groups": ["layout", "formatting"]}),
        ToolSpec("format_range", "format_range", "format", {}, {"groups": ["layout", "formatting"]}),
        ToolSpec("read_data_from_excel", "read_data_from_excel", "read", {}, {"groups": ["readback", "inspection"]}),
        ToolSpec("get_workbook_metadata", "get_workbook_metadata", "meta", {}, {"groups": ["workbook", "inspection"]}),
        ToolSpec("create_chart", "create_chart", "chart", {}, {"groups": ["chart", "analytics"]}),
    ]
    backend = SimpleNamespace(backend_id="zcp_client_to_native_zcp")
    routed = _tool_specs_for_turn(
        backend=backend,
        case=case,
        prompt="修复董事会概览页并整理标题区",
        all_tool_specs=tools,
        turn_index=1,
    )
    expanded = _tool_specs_for_turn(
        backend=backend,
        case=case,
        prompt="修复董事会概览页并整理标题区",
        all_tool_specs=tools,
        turn_index=11,
    )
    assert len(routed) < len(tools)
    assert len(expanded) == len(tools)


def test_tool_specs_for_turn_widens_on_loop_detection() -> None:
    case = next(item for item in tier_d_cases() if item.case_id == "executive_briefing_goal")
    tools = [
        ToolSpec("create_workbook", "create_workbook", "create", {}, {"groups": ["workbook", "create"], "stages": ["setup"]}),
        ToolSpec("create_worksheet", "create_worksheet", "sheet", {}, {"groups": ["worksheet", "create"], "stages": ["setup"]}),
        ToolSpec("write_data_to_excel", "write_data_to_excel", "write", {}, {"groups": ["write", "worksheet"], "stages": ["operate"]}),
        ToolSpec("read_data_from_excel", "read_data_from_excel", "read", {}, {"groups": ["readback", "inspection"], "stages": ["verify"]}),
        ToolSpec("create_chart", "create_chart", "chart", {}, {"groups": ["chart", "analytics"], "stages": ["analytics"]}),
    ]
    backend = SimpleNamespace(backend_id="zcp_client_to_native_zcp")
    widened = _tool_specs_for_turn(
        backend=backend,
        case=case,
        prompt="准备管理层简报并补全摘要页",
        all_tool_specs=tools,
        turn_index=1,
        tool_history=["write_data_to_excel", "write_data_to_excel", "write_data_to_excel", "write_data_to_excel", "write_data_to_excel", "write_data_to_excel"],
    )
    assert len(widened) == len(tools)


def test_native_subset_keeps_required_tools_even_if_not_keyword_matched() -> None:
    case = SimpleNamespace(autonomous=True, required_tool_calls={"validate_formula_syntax": 1})
    tools = [
        ToolSpec("validate_formula_syntax", "validate_formula_syntax", "validate", {}, {"groups": ["formula", "validation"]}),
        ToolSpec("create_chart", "create_chart", "chart", {}, {"groups": ["chart", "analytics"]}),
        ToolSpec("read_data_from_excel", "read_data_from_excel", "read", {}, {"groups": ["readback", "inspection"]}),
    ]
    subset = _native_zcp_initial_subset(case, "请检查这个账本是否可发布", tools)
    names = {tool.source_name for tool in subset}
    assert "validate_formula_syntax" in names


def test_evaluate_tool_history_uses_native_required_calls_for_native_backend() -> None:
    case = SimpleNamespace(
        required_tool_calls={"create_workbook": 1, "create_worksheet": 1},
        native_zcp_required_tool_calls={"zcp_finalize_executive_briefing": 1},
    )
    assert evaluate_tool_history(case, ["zcp_finalize_executive_briefing"], backend_id="zcp_client_to_native_zcp")
    assert not evaluate_tool_history(case, ["create_workbook", "create_worksheet"], backend_id="zcp_client_to_native_zcp")
    assert evaluate_tool_history(case, ["create_workbook", "create_worksheet"], backend_id="mcp_client_to_original_mcp")


def test_native_zcp_routing_prefers_semantic_workflow_tool_when_available() -> None:
    case = next(item for item in tier_d_cases() if item.case_id == "executive_briefing_goal")
    tools = [
        ToolSpec("zcp_finalize_executive_briefing", "zcp_finalize_executive_briefing", "workflow", {}, {"groups": ["workflow"], "stages": ["operate"]}),
        ToolSpec("create_workbook", "create_workbook", "create", {}, {"groups": ["workbook", "create"], "stages": ["setup"]}),
        ToolSpec("write_data_to_excel", "write_data_to_excel", "write", {}, {"groups": ["write", "worksheet"], "stages": ["operate"]}),
        ToolSpec("format_range", "format_range", "format", {}, {"groups": ["layout", "formatting"], "stages": ["polish"]}),
        ToolSpec("read_data_from_excel", "read_data_from_excel", "read", {}, {"groups": ["readback", "inspection"], "stages": ["verify"]}),
    ]
    backend = SimpleNamespace(backend_id="zcp_client_to_native_zcp")
    subset = _tool_specs_for_turn(
        backend=backend,
        case=case,
        prompt="请准备运营周会简报并输出最终结果",
        all_tool_specs=tools,
        turn_index=1,
    )
    names = [tool.source_name for tool in subset]
    assert "zcp_finalize_executive_briefing" in names
    assert "create_workbook" not in names
    assert "write_data_to_excel" not in names


def test_tier_c_native_semantic_tool_compliance_and_routing() -> None:
    case = next(item for item in tier_c_cases() if item.case_id == "finance_close_summary")
    semantic_tool = next(iter(case.native_zcp_required_tool_calls or {}))
    assert evaluate_tool_history(case, [semantic_tool], backend_id="zcp_client_to_native_zcp")
    assert not evaluate_tool_history(case, ["create_workbook", "create_worksheet"], backend_id="zcp_client_to_native_zcp")
    assert evaluate_tool_history(case, ["create_workbook", "create_worksheet", "write_data_to_excel", "apply_formula", "apply_formula", "apply_formula", "format_range", "create_table", "read_data_from_excel", "validate_formula_syntax"], backend_id="mcp_client_to_original_mcp")

    tools = [
        ToolSpec(semantic_tool, semantic_tool, "workflow", {}, {"groups": ["workflow"], "stages": ["operate"]}),
        ToolSpec("create_workbook", "create_workbook", "create", {}, {"groups": ["workbook", "create"], "stages": ["setup"]}),
        ToolSpec("write_data_to_excel", "write_data_to_excel", "write", {}, {"groups": ["write", "worksheet"], "stages": ["operate"]}),
        ToolSpec("apply_formula", "apply_formula", "formula", {}, {"groups": ["formula", "write"], "stages": ["calculate"]}),
        ToolSpec("read_data_from_excel", "read_data_from_excel", "read", {}, {"groups": ["readback", "inspection"], "stages": ["verify"]}),
    ]
    backend = SimpleNamespace(backend_id="zcp_client_to_native_zcp")
    subset = _tool_specs_for_turn(
        backend=backend,
        case=case,
        prompt="请完成财务关账汇总表",
        all_tool_specs=tools,
        turn_index=1,
    )
    names = [tool.source_name for tool in subset]
    assert semantic_tool in names
    assert "create_workbook" not in names
    assert "apply_formula" not in names


def test_tier_b_native_semantic_tool_compliance_and_routing() -> None:
    case = next(item for item in tier_b_cases() if item.case_id == "tier_b_formula_flow_chain")
    semantic_tool = next(iter(case.native_zcp_required_tool_calls or {}))
    assert evaluate_tool_history(case, [semantic_tool], backend_id="zcp_client_to_native_zcp")
    assert not evaluate_tool_history(case, ["validate_formula_syntax", "apply_formula", "apply_formula"], backend_id="zcp_client_to_native_zcp")
    assert evaluate_tool_history(case, ["validate_formula_syntax", "apply_formula", "apply_formula", "read_data_from_excel"], backend_id="mcp_client_to_original_mcp")

    tools = [
        ToolSpec(semantic_tool, semantic_tool, "workflow", {}, {"groups": ["workflow"], "stages": ["operate"]}),
        ToolSpec("validate_formula_syntax", "validate_formula_syntax", "validate", {}, {"groups": ["formula", "validation"], "stages": ["verify"]}),
        ToolSpec("apply_formula", "apply_formula", "formula", {}, {"groups": ["formula", "write"], "stages": ["calculate"]}),
        ToolSpec("read_data_from_excel", "read_data_from_excel", "read", {}, {"groups": ["readback", "inspection"], "stages": ["verify"]}),
    ]
    backend = SimpleNamespace(backend_id="zcp_client_to_native_zcp")
    subset = _tool_specs_for_turn(
        backend=backend,
        case=case,
        prompt="请完成公式流校验并输出最终 JSON",
        all_tool_specs=tools,
        turn_index=1,
    )
    names = [tool.source_name for tool in subset]
    assert names == [semantic_tool]
