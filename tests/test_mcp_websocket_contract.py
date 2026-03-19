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


def test_official_mcp_client_can_talk_to_zcp_websocket_server() -> None:
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
from mcp.client.session import ClientSession
from mcp.client.websocket import websocket_client


async def main():
    async with websocket_client("ws://127.0.0.1:{port}/ws?token=demo-token") as streams:
        read_stream, write_stream = streams
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            tool = await session.call_tool("weather.get_current", {{"city": "Hangzhou"}})
            print(json.dumps({{
                "tool_names": [item.name for item in tools.tools],
                "tool_city": tool.structured_content["city"] if tool.structured_content else tool.content[0].text,
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
        assert "Hangzhou" in str(payload["tool_city"])
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
