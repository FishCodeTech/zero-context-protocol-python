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
BENCH_VENV = ROOT / ".venv-bench" / "bin" / "python"


def maybe_reexec() -> None:
    if sys.version_info >= MIN_PYTHON:
        return
    if BENCH_VENV.exists():
        os.execv(str(BENCH_VENV), [str(BENCH_VENV), __file__, *sys.argv[1:]])
    raise SystemExit("Python >= 3.10 is required. Create /Users/bytedance/Desktop/agent/ZCP/.venv-bench first.")


maybe_reexec()

from tools.benchmarking import (
    DEFAULT_API_KEY,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_REPEATS,
    ProgressEvent,
    markdown_report,
    print_summary_table,
    run_protocol_benchmark,
    write_reports,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare ZCP and MCP-style tool-calling against the same model.")
    parser.add_argument("--api-key", default=DEFAULT_API_KEY)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--output-dir", default=str(ROOT / "benchmark_reports"))
    return parser.parse_args()


def log_progress(event: ProgressEvent) -> None:
    prefix = (
        f"[{event.protocol} {event.overall_index}/{event.overall_total}] "
        f"repeat={event.repeat_index}/{event.total_repeats} case={event.case_id}"
    )
    if event.phase == "start":
        print(f"{prefix} started", flush=True)
        return
    status = "ok" if not event.error else f"error={event.error}"
    print(
        f"{prefix} finished status={status} tokens={event.total_tokens} turns={event.turns} "
        f"answer_ok={event.answer_ok} tool_ok={event.tool_ok} elapsed={event.elapsed_seconds:.1f}s",
        flush=True,
    )


def main() -> None:
    args = parse_args()
    print(f"starting benchmark model={args.model} repeats={args.repeats}", flush=True)
    zcp_runs = run_protocol_benchmark(
        "zcp",
        api_key=args.api_key,
        base_url=args.base_url,
        model=args.model,
        repeats=args.repeats,
        temperature=args.temperature,
        progress=log_progress,
    )
    mcp_runs = run_protocol_benchmark(
        "mcp",
        api_key=args.api_key,
        base_url=args.base_url,
        model=args.model,
        repeats=args.repeats,
        temperature=args.temperature,
        progress=log_progress,
        python_executable=sys.executable,
        mcp_server_script=str(ROOT / "examples" / "real_sdk_mcp_server.py"),
    )
    runs = [*zcp_runs, *mcp_runs]
    print(print_summary_table(runs))
    print()
    print(markdown_report(runs, model=args.model, repeats=args.repeats))
    markdown_path, json_path = write_reports(runs, output_dir=args.output_dir, model=args.model, repeats=args.repeats)
    print()
    print(f"markdown_report={markdown_path}")
    print(f"json_report={json_path}")


if __name__ == "__main__":
    main()
