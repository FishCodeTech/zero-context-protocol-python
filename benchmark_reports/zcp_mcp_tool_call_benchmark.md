# ZCP vs MCP Real SDK Tool-Call Benchmark

- model: `deepseek-chat`
- repeats: `2`
- cases per protocol: `4`

## Summary

| Protocol | Runs | Answer Accuracy | Tool Compliance | Avg Prompt Tokens | Avg Completion Tokens | Avg Total Tokens |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| mcp | 8 | 100.0% | 100.0% | 4136.1 | 367.8 | 4503.9 |
| zcp | 8 | 100.0% | 100.0% | 2577.5 | 255.5 | 2833.0 |

## Case Breakdown

| Case | ZCP Avg Total | MCP Avg Total | MCP-ZCP | MCP / ZCP | ZCP Accuracy | MCP Accuracy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| warmer_city_delta | 2821.0 | 4579.5 | 1758.5 | 1.62x | 100.0% | 100.0% |
| shanghai_temp_f_and_humidity | 2565.0 | 3834.5 | 1269.5 | 1.49x | 100.0% | 100.0% |
| average_three_city_temperature | 3116.0 | 5237.5 | 2121.5 | 1.68x | 100.0% | 100.0% |
| more_humid_city_delta | 2830.0 | 4364.0 | 1534.0 | 1.54x | 100.0% | 100.0% |