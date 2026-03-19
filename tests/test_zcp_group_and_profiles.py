import asyncio

from zcp import FastZCP, OpenAIAdapter, ZCPSessionGroup, stdio_client, stdio_server
from zcp.profiles.oai import OpenAIAdapter as ProfileOpenAIAdapter


def build_session(tool_name: str):
    app = FastZCP(tool_name)

    @app.tool(
        name=tool_name,
        description=f"Tool {tool_name}",
        input_schema={
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
            "additionalProperties": False,
        },
        output_mode="scalar",
        inline_ok=True,
    )
    def tool(city: str):
        return {"city": city, "tool": tool_name}

    @app.resource(f"{tool_name}://resource", name=f"{tool_name}-resource")
    def resource():
        return {"tool": tool_name}

    @app.prompt(name=f"{tool_name}.prompt")
    def prompt():
        return [{"role": "user", "content": tool_name}]

    return stdio_client(stdio_server(app))


def test_session_group_aggregates_servers() -> None:
    session_group = ZCPSessionGroup([build_session("weather.get_current"), build_session("calendar.find_slots")])

    async def run():
        tools = await session_group.list_tools()
        resources = await session_group.list_resources()
        prompts = await session_group.list_prompts()
        return tools, resources, prompts

    tools, resources, prompts = asyncio.run(run())
    assert len(tools["tools"]) == 2
    assert len(resources["resources"]) == 2
    assert len(prompts["prompts"]) == 2


def test_oai_profile_exports_zcp_adapter() -> None:
    from zcp.adapters.openai import OpenAIAdapter as DirectOpenAIAdapter

    assert OpenAIAdapter is DirectOpenAIAdapter
    assert ProfileOpenAIAdapter is DirectOpenAIAdapter
