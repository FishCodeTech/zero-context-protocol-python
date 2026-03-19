from zcp import OpenAIStrictSchemaCompiler, ToolDefinition


def test_compile_openai_strict_schema_marks_optional_fields_nullable() -> None:
    tool = ToolDefinition(
        tool_id="17",
        alias="web.search",
        description_short="Search the web.",
        input_schema={
            "type": "object",
            "properties": {
                "q": {"type": "string"},
                "k": {"type": "integer"},
                "opts": {
                    "type": "object",
                    "properties": {
                        "lang": {"type": "string"},
                    },
                    "required": [],
                },
            },
            "required": ["q"],
        },
    )

    compiler = OpenAIStrictSchemaCompiler()
    compiled = compiler.compile_tool(tool)

    params = compiled["parameters"]
    assert params["additionalProperties"] is False
    assert params["required"] == ["q", "k", "opts"]
    assert params["properties"]["k"]["type"] == ["integer", "null"]
    assert params["properties"]["opts"]["type"] == ["object", "null"]
    assert params["properties"]["opts"]["properties"]["lang"]["type"] == ["string", "null"]
    assert params["properties"]["opts"]["required"] == ["lang"]
