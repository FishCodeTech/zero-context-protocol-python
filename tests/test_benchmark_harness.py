from tools.benchmarking import benchmark_cases, evaluate_case_output, evaluate_tool_history, parse_json_object


def _case(case_id: str):
    return next(case for case in benchmark_cases() if case.case_id == case_id)


def test_parse_json_object_from_fenced_text() -> None:
    parsed = parse_json_object("```json\n{\"city\":\"Shanghai\",\"temp_f\":71.6,\"humidity\":81}\n```")
    assert parsed == {"city": "Shanghai", "temp_f": 71.6, "humidity": 81}


def test_evaluate_case_output_accepts_expected_payload() -> None:
    case = _case("average_three_city_temperature")
    assert evaluate_case_output(
        case,
        {
            "avg_temp_c": 22.3,
            "cities": ["Beijing", "Shanghai", "Shenzhen"],
        },
    )


def test_evaluate_tool_history_checks_minimum_counts() -> None:
    case = _case("warmer_city_delta")
    assert evaluate_tool_history("zcp", case, ["weather.get_current", "weather.get_current", "math.subtract"])
    assert not evaluate_tool_history("mcp", case, ["get_weather", "subtract_numbers"])
