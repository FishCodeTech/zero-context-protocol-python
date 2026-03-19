# Excel LLM Token Benchmark

- model: `deepseek-chat`
- repeats: `1`
- total cases: `37`

## Overall Summary

| Backend | Client | Server Mode | Runs | Answer | Workbook | Tool | Avg Prompt | Avg Completion | Avg Total | Avg Turns | Avg Tool Calls | Avg Extra Calls | Planning Eff. |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| mcp_client_to_zcp_mcp_surface | mcp_client | native_zcp | 37 | 97.3% | 91.9% | 73.0% | 30253.4 | 470.3 | 30723.7 | 3.9 | 3.0 | 0.8 | 2.37 |
| zcp_client_to_native_zcp | zcp_client | native_zcp | 37 | 100.0% | 97.3% | 100.0% | 7864.6 | 163.3 | 8027.9 | 2.1 | 1.1 | 0.1 | 0.97 |

## Tier Summary

| Tier | Backend | Runs | Answer | Workbook | Tool | Avg Total | Avg Turns | Avg Tool Calls | Avg Extra Calls | Planning Eff. | Autonomous Rate |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A | mcp_client_to_zcp_mcp_surface | 16 | 100.0% | 93.8% | 100.0% | 17613.2 | 2.4 | 1.4 | 0.4 | 0.82 | 0.0% |
| A | zcp_client_to_native_zcp | 16 | 100.0% | 93.8% | 100.0% | 15979.4 | 2.2 | 1.2 | 0.2 | 0.93 | 0.0% |
| B | mcp_client_to_zcp_mcp_surface | 8 | 100.0% | 100.0% | 75.0% | 29239.4 | 3.9 | 3.0 | 0.5 | 1.70 | 0.0% |
| B | zcp_client_to_native_zcp | 8 | 100.0% | 100.0% | 100.0% | 1826.6 | 2.0 | 1.0 | 0.0 | 1.00 | 0.0% |
| C | mcp_client_to_zcp_mcp_surface | 7 | 85.7% | 71.4% | 57.1% | 72113.9 | 8.7 | 7.9 | 1.7 | 3.61 | 0.0% |
| C | zcp_client_to_native_zcp | 7 | 100.0% | 100.0% | 100.0% | 2091.1 | 2.0 | 1.0 | 0.0 | 1.00 | 0.0% |
| D | mcp_client_to_zcp_mcp_surface | 6 | 100.0% | 100.0% | 16.7% | 19375.7 | 2.5 | 1.5 | 1.0 | 5.96 | 100.0% |
| D | zcp_client_to_native_zcp | 6 | 100.0% | 100.0% | 100.0% | 2018.3 | 2.0 | 1.0 | 0.0 | 1.00 | 100.0% |

## Pairwise Comparison

| Scope | Comparison | Left Avg Total | Right Avg Total | Token Delta | Ratio |
| --- | --- | ---: | ---: | ---: | ---: |
| overall | native_zcp_zcp_vs_mcp_surface | 8027.9 | 30723.7 | 22695.8 | 3.83x |
| A | native_zcp_zcp_vs_mcp_surface | 15979.4 | 17613.2 | 1633.8 | 1.10x |
| B | native_zcp_zcp_vs_mcp_surface | 1826.6 | 29239.4 | 27412.8 | 16.01x |
| C | native_zcp_zcp_vs_mcp_surface | 2091.1 | 72113.9 | 70022.7 | 34.49x |
| D | native_zcp_zcp_vs_mcp_surface | 2018.3 | 19375.7 | 17357.3 | 9.60x |

## Case Breakdown

| Tier | Case | ZCP->Original MCP | MCP->Original MCP | ZCP->Native ZCP | MCP->ZCP MCP Surface |
| --- | --- | ---: | ---: | ---: | ---: |
| A | tier_a_apply_formula | 0.0 | 0.0 | 14328.0 | 14114.0 |
| A | tier_a_copy_worksheet | 0.0 | 0.0 | 14266.0 | 14062.0 |
| A | tier_a_create_table | 0.0 | 0.0 | 14318.0 | 21482.0 |
| A | tier_a_create_workbook | 0.0 | 0.0 | 14289.0 | 14019.0 |
| A | tier_a_create_worksheet | 0.0 | 0.0 | 14209.0 | 14012.0 |
| A | tier_a_delete_sheet_columns | 0.0 | 0.0 | 22272.0 | 31172.0 |
| A | tier_a_delete_sheet_rows | 0.0 | 0.0 | 32199.0 | 30557.0 |
| A | tier_a_format_range | 0.0 | 0.0 | 14343.0 | 21528.0 |
| A | tier_a_get_merged_cells | 0.0 | 0.0 | 14246.0 | 14052.0 |
| A | tier_a_insert_columns | 0.0 | 0.0 | 14324.0 | 14103.0 |
| A | tier_a_insert_rows | 0.0 | 0.0 | 14316.0 | 14086.0 |
| A | tier_a_merge_cells | 0.0 | 0.0 | 14320.0 | 14115.0 |
| A | tier_a_read_data | 0.0 | 0.0 | 15248.0 | 14682.0 |
| A | tier_a_rename_worksheet | 0.0 | 0.0 | 14273.0 | 21512.0 |
| A | tier_a_validate_formula_syntax | 0.0 | 0.0 | 14363.0 | 14157.0 |
| A | tier_a_write_data | 0.0 | 0.0 | 14356.0 | 14158.0 |
| B | tier_b_column_maintenance_chain | 0.0 | 0.0 | 1813.0 | 14148.0 |
| B | tier_b_formula_flow_chain | 0.0 | 0.0 | 1944.0 | 31167.0 |
| B | tier_b_layout_flow_chain | 0.0 | 0.0 | 1796.0 | 45589.0 |
| B | tier_b_readback_verification_chain | 0.0 | 0.0 | 1827.0 | 31464.0 |
| B | tier_b_row_maintenance_chain | 0.0 | 0.0 | 1837.0 | 37915.0 |
| B | tier_b_sheet_maintenance_chain | 0.0 | 0.0 | 1775.0 | 29060.0 |
| B | tier_b_table_setup_chain | 0.0 | 0.0 | 1837.0 | 30457.0 |
| B | tier_b_workbook_bootstrap_chain | 0.0 | 0.0 | 1784.0 | 14115.0 |
| C | finance_close_summary | 0.0 | 0.0 | 2086.0 | 118913.0 |
| C | headcount_plan_restructure | 0.0 | 0.0 | 2067.0 | 104899.0 |
| C | kpi_dashboard_sheet | 0.0 | 0.0 | 2043.0 | 81101.0 |
| C | multi_sheet_monthly_rollup | 0.0 | 0.0 | 2164.0 | 14481.0 |
| C | revenue_model_revision | 0.0 | 0.0 | 2090.0 | 91011.0 |
| C | sales_ops_weekly_pack | 0.0 | 0.0 | 2174.0 | 14501.0 |
| C | template_copy_and_fill | 0.0 | 0.0 | 2014.0 | 79891.0 |
| D | board_packet_repair_goal | 0.0 | 0.0 | 1895.0 | 14218.0 |
| D | executive_briefing_goal | 0.0 | 0.0 | 2174.0 | 14302.0 |
| D | month_end_close_goal | 0.0 | 0.0 | 1894.0 | 14216.0 |
| D | requests_register_goal | 0.0 | 0.0 | 2096.0 | 14196.0 |
| D | staging_cleanup_goal | 0.0 | 0.0 | 2109.0 | 45000.0 |
| D | team_plan_snapshot_goal | 0.0 | 0.0 | 1942.0 | 14322.0 |