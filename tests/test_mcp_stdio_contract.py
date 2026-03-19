import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BENCH_PYTHON = ROOT.parent / ".venv-bench" / "bin" / "python"
SERVER_SCRIPT = ROOT / "examples" / "run_zcp_mcp_stdio_server.py"


def test_official_mcp_client_can_talk_to_zcp_stdio_server() -> None:
    if not BENCH_PYTHON.exists():
        return

    script = f"""
import asyncio
import json
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def main():
    async with stdio_client(
        StdioServerParameters(
            command={json.dumps(str(BENCH_PYTHON))},
            args={[str(SERVER_SCRIPT)]!r},
            cwd={json.dumps(str(ROOT))},
        )
    ) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            tool = await session.call_tool("weather.get_current", {{"city": "Hangzhou"}})
            resources = await session.list_resources()
            templates = await session.list_resource_templates()
            resource = await session.read_resource("weather://cities")
            prompts = await session.list_prompts()
            prompt = await session.get_prompt("weather.summary", {{"city": "Hangzhou"}})
            completion = await session.complete(
                {{"type": "ref/prompt", "name": "weather.summary"}},
                {{"name": "city", "value": "Ha"}},
            )
            tool_payload = tool.structured_content
            if tool_payload is None and tool.content:
                tool_payload = {{"text": tool.content[0].text}}
            print(json.dumps({{
                "tool_names": [item.name for item in tools.tools],
                "tool_content": tool_payload,
                "resource_uris": [item.uri for item in resources.resources],
                "template_uris": [item.uri_template for item in templates.resource_templates],
                "resource_text": resource.contents[0].text,
                "prompt_names": [item.name for item in prompts.prompts],
                "prompt_content": prompt.messages[0].content.text,
                "completion_values": completion.completion.values,
            }}, ensure_ascii=False))


asyncio.run(main())
"""
    completed = subprocess.run(
        [str(BENCH_PYTHON), "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(completed.stdout.strip())
    assert "weather.get_current" in payload["tool_names"]
    if "city" in payload["tool_content"]:
        assert payload["tool_content"]["city"] == "Hangzhou"
    else:
        assert "Hangzhou" in payload["tool_content"]["text"]
    assert "weather://cities" in payload["resource_uris"]
    assert "weather://city/{name}" in payload["template_uris"]
    assert "Hangzhou" in payload["resource_text"]
    assert "weather.summary" in payload["prompt_names"]
    assert payload["prompt_content"] == "Summarize weather for Hangzhou"
    assert payload["completion_values"] == ["Hangzhou"]
