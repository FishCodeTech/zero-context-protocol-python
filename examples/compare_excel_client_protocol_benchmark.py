#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MIN_PYTHON = (3, 10)
BENCH_VENV = ROOT.parent / ".venv-bench" / "bin" / "python"


def maybe_reexec() -> None:
    if sys.version_info >= MIN_PYTHON:
        return
    if BENCH_VENV.exists():
        os.execv(str(BENCH_VENV), [str(BENCH_VENV), __file__, *sys.argv[1:]])
    raise SystemExit("Python >= 3.10 is required. Create a local .venv-bench first.")


maybe_reexec()

from tools.excel_llm_benchmarking import (
    DEFAULT_API_KEY,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_REPEATS,
    DEFAULT_EXCEL_REPO,
    ExcelLLMProgressEvent,
    backend_ids,
    markdown_report,
    print_summary_table,
    run_excel_llm_benchmark,
    write_reports,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark LLM token usage for MCP and ZCP clients across original Excel MCP and native ZCP server modes."
    )
    parser.add_argument("--api-key", default=DEFAULT_API_KEY)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--python-executable", default=str(ROOT.parent / ".venv-bench" / "bin" / "python"))
    parser.add_argument("--excel-repo", default=str(DEFAULT_EXCEL_REPO))
    parser.add_argument("--output-dir", default=str(ROOT / "benchmark_reports"))
    parser.add_argument("--tiers", default="A,B,C,D", help="Comma-separated tiers to run, e.g. A,B or D")
    parser.add_argument("--case-limit", type=int, default=None, help="Optional limit after tier filtering for smoke runs")
    parser.add_argument(
        "--backends",
        default=",".join(backend_ids()),
        help="Comma-separated backend ids to run",
    )
    parser.add_argument("--checkpoint-path", default=None, help="Optional JSONL checkpoint path for append/resume")
    return parser.parse_args()


def log_progress(event: ExcelLLMProgressEvent) -> None:
    prefix = (
        f"[{event.backend_id} {event.overall_index}/{event.overall_total}] "
        f"repeat={event.repeat_index}/{event.total_repeats} tier={event.tier} case={event.case_id}"
    )
    if event.phase == "start":
        print(f"{prefix} started", flush=True)
        return
    status = "ok" if not event.error else f"error={event.error}"
    print(
        f"{prefix} finished status={status} tokens={event.total_tokens} turns={event.turns} "
        f"answer_ok={event.answer_ok} workbook_ok={event.workbook_ok} tool_ok={event.tool_ok} "
        f"elapsed={event.elapsed_seconds:.1f}s",
        flush=True,
    )


def main() -> None:
    args = parse_args()
    print(
        "starting excel llm token benchmark "
        f"model={args.model} repeats={args.repeats} tiers={args.tiers} base_url={args.base_url}",
        flush=True,
    )
    runs = run_excel_llm_benchmark(
        api_key=args.api_key,
        base_url=args.base_url,
        model=args.model,
        repeats=args.repeats,
        temperature=args.temperature,
        python_executable=args.python_executable,
        excel_repo=Path(args.excel_repo).resolve(),
        tiers=[item.strip().upper() for item in args.tiers.split(",") if item.strip()],
        case_limit=args.case_limit,
        backends=[item.strip() for item in args.backends.split(",") if item.strip()],
        checkpoint_path=args.checkpoint_path,
        progress=log_progress,
    )
    print(print_summary_table(runs))
    print()
    print(markdown_report(runs, model=args.model, repeats=args.repeats))
    markdown_path, json_path = write_reports(runs, output_dir=args.output_dir, model=args.model, repeats=args.repeats)
    print()
    print(f"markdown_report={markdown_path}")
    print(f"json_report={json_path}")


if __name__ == "__main__":
    main()
