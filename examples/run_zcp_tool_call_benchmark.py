#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.benchmarking import (
    DEFAULT_API_KEY,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_REPEATS,
    ProgressEvent,
    json_report,
    print_summary_table,
    run_protocol_benchmark,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the ZCP-only tool-call benchmark.")
    parser.add_argument("--api-key", default=DEFAULT_API_KEY)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--temperature", type=float, default=0.0)
    return parser.parse_args()


def log_progress(event: ProgressEvent) -> None:
    prefix = f"[{event.overall_index}/{event.overall_total}] {event.protocol} repeat={event.repeat_index}/{event.total_repeats} case={event.case_id}"
    if event.phase == "start":
        print(f"{prefix} ...", flush=True)
        return
    status = "ok" if not event.error else f"error={event.error}"
    print(
        f"{prefix} done status={status} tokens={event.total_tokens} turns={event.turns} "
        f"answer_ok={event.answer_ok} tool_ok={event.tool_ok} elapsed={event.elapsed_seconds:.1f}s",
        flush=True,
    )


def main() -> None:
    args = parse_args()
    runs = run_protocol_benchmark(
        "zcp",
        api_key=args.api_key,
        base_url=args.base_url,
        model=args.model,
        repeats=args.repeats,
        temperature=args.temperature,
        progress=log_progress,
    )
    print(print_summary_table(runs))
    print()
    print(json.dumps(json_report(runs, model=args.model, repeats=args.repeats), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
