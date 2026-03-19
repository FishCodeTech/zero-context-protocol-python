# Semantic Benchmark Summary v5

Model: `deepseek-chat`  
Repeats: `1`  
Backends:

- `zcp_client_to_native_zcp`
- `mcp_client_to_zcp_mcp_surface`

## Headline

Native ZCP now wins across every tier in the current Excel benchmark suite.

The important shift is architectural:

- Tier B now uses semantic chain tools
- Tier C uses semantic workflow tools
- Tier D uses semantic autonomous workflow tools
- Native ZCP routing now exposes only the semantic workflow tool when one exists, instead of mixing in extra metadata tools

This changes the model workload from "assemble primitive operations" to "select the correct workflow action".

## Overall

| Backend | Answer | Workbook | Tool | Avg Total | Avg Turns | Avg Tool Calls |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `zcp_client_to_native_zcp` | 100.0% | 97.3% | 100.0% | 8027.9 | 2.1 | 1.1 |
| `mcp_client_to_zcp_mcp_surface` | 97.3% | 91.9% | 73.0% | 30723.7 | 3.9 | 3.0 |

Overall native-ZCP advantage:

- token delta: `22695.8`
- ratio: `3.83x`

## Tier Results

| Tier | Native ZCP Avg Total | MCP Surface Avg Total | Ratio | Native ZCP Quality |
| --- | ---: | ---: | ---: | --- |
| `A` | 15979.4 | 17613.2 | `1.10x` | `100.0 / 93.8 / 100.0` |
| `B` | 1826.6 | 29239.4 | `16.01x` | `100.0 / 100.0 / 100.0` |
| `C` | 2091.1 | 72113.9 | `34.49x` | `100.0 / 100.0 / 100.0` |
| `D` | 2018.3 | 19375.7 | `9.60x` | `100.0 / 100.0 / 100.0` |

`Native ZCP Quality` is `answer / workbook / tool`.

## What Improved Since v4

The key change between the previous full run and this run:

- Tier B semantic chain tools were added
- native routing was tightened so semantic tools are exposed alone by default

That moved Tier B from partial win to decisive win:

- before: native ZCP had lower tokens but some workbook/tool misses
- now: `100 / 100 / 100` with `16.01x` token advantage

## Interpretation

### Tier A

Tier A is still primitive-operation territory, but native ZCP now slightly wins there too.

### Tier B

Tier B is now the clearest proof that semantic chain tools matter:

- average tokens dropped to `1826.6`
- MCP surface stayed at `29239.4`
- every Tier B case completed with perfect accuracy and tool compliance on native ZCP

### Tier C

Tier C is the biggest workflow win:

- native ZCP averages only `2091.1`
- MCP surface averages `72113.9`

### Tier D

Tier D remains strongly in favor of native ZCP:

- native ZCP averages `2018.3`
- MCP surface averages `19375.7`

## Conclusion

The benchmark story is now materially different from the earlier state:

- ZCP is no longer just protocol-compatible with MCP
- ZCP now demonstrates a workflow-layer advantage
- The advantage is strongest when the server exposes semantic operations and the native client routes directly to them

In practical terms:

- native ZCP is now better overall
- native ZCP wins every tier in the current benchmark suite
- medium and complex Excel scenarios are now overwhelmingly in favor of native ZCP
