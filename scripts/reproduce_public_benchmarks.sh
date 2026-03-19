#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

: "${OPENAI_API_KEY:?Set OPENAI_API_KEY before running benchmarks.}"
OPENAI_BASE_URL="${OPENAI_BASE_URL:-https://api.deepseek.com}"
OPENAI_MODEL="${OPENAI_MODEL:-deepseek-chat}"

python3 "$ROOT/examples/compare_zcp_mcp_tool_call_benchmark.py" \
  --api-key "$OPENAI_API_KEY" \
  --base-url "$OPENAI_BASE_URL" \
  --model "$OPENAI_MODEL" \
  --repeats 2

python3 "$ROOT/examples/compare_excel_client_protocol_benchmark.py" \
  --api-key "$OPENAI_API_KEY" \
  --base-url "$OPENAI_BASE_URL" \
  --model "$OPENAI_MODEL" \
  --repeats 1 \
  --tiers A,B,C,D \
  --backends zcp_client_to_native_zcp,mcp_client_to_zcp_mcp_surface \
  --output-dir "$ROOT/benchmark_reports/full_semantic_compare_v5"
