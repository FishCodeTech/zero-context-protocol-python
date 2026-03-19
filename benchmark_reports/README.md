# Published Benchmark Artifacts

This directory contains published benchmark artifacts and, during local
development, may also contain intermediate run outputs.

## Official Public Reports

Use these artifacts as the public source of truth:

- compact tool-call benchmark:
  - `zcp_mcp_tool_call_benchmark.md`
  - `zcp_mcp_tool_call_benchmark.json`
- Excel semantic workflow benchmark:
  - `full_semantic_compare_v5/excel_llm_token_benchmark.md`
  - `full_semantic_compare_v5/excel_llm_token_benchmark.json`
  - `full_semantic_compare_v5/semantic_benchmark_summary.md`

## Non-Public Or Intermediate Artifacts

Do not treat checkpoint files, smoke runs, or older comparison directories as
official release evidence. Those are for local iteration only and should be
removed or archived before a public release.

## Reproducing Public Benchmarks

Use the helper scripts in [`scripts`](../scripts)
or the benchmark entrypoints under [`examples`](../examples).
