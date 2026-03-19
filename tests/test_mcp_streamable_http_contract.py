import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BENCH_PYTHON = ROOT.parent / ".venv-bench" / "bin" / "python"
SERVER_SCRIPT = ROOT / "examples" / "run_zcp_api_server.py"


def _wait_for_health(port: int, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=0.5) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("server did not become ready")


def test_official_mcp_client_can_talk_to_zcp_streamable_http_server() -> None:
    if not BENCH_PYTHON.exists():
        return

    port = 8000
    env = dict(os.environ)
    env["PORT"] = str(port)
    server = subprocess.Popen(
        [str(BENCH_PYTHON), str(SERVER_SCRIPT)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_health(port)
        script = f"""
import asyncio
import json
import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main():
    http_client = httpx.AsyncClient(headers={{"authorization": "Bearer replace-me-in-production"}})
    async with streamable_http_client("http://127.0.0.1:{port}/mcp", http_client=http_client) as streams:
        read_stream, write_stream = streams
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            tool = await session.call_tool("weather.get_current", {{"city": "Hangzhou"}})
            resources = await session.list_resources()
            print(json.dumps({{
                "tool_names": [item.name for item in tools.tools],
                "tool_city": tool.structured_content["city"],
                "resource_uris": [item.uri for item in resources.resources],
            }}, ensure_ascii=False))
    await http_client.aclose()


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
        assert payload["tool_city"] == "Hangzhou"
        assert "weather://cities" in payload["resource_uris"]
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
