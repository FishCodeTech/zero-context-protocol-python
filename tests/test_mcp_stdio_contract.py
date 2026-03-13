import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BENCH_PYTHON = ROOT / ".venv-bench" / "bin" / "python"
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
            resource = await session.read_resource("weather://cities")
            prompts = await session.list_prompts()
            prompt = await session.get_prompt("weather.summary", {{"city": "Hangzhou"}})
            print(json.dumps({{
                "tool_names": [item.name for item in tools.tools],
                "tool_content": tool.structured_content,
                "resource_uris": [item.uri for item in resources.resources],
                "resource_text": resource.contents[0].text,
                "prompt_names": [item.name for item in prompts.prompts],
                "prompt_content": prompt.messages[0].content.text,
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
    assert payload["tool_content"]["city"] == "Hangzhou"
    assert "weather://cities" in payload["resource_uris"]
    assert "Hangzhou" in payload["resource_text"]
    assert "weather.summary" in payload["prompt_names"]
    assert payload["prompt_content"] == "Summarize weather for Hangzhou"
