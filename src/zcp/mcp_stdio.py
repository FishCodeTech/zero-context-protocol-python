from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from .gateway import MCPGatewayServer
from .server import FastZCP
from .transports import stdio_server


async def run_mcp_stdio_server(app: FastZCP, *, session_id: str = "mcp-stdio") -> None:
    gateway = MCPGatewayServer(stdio_server(app, session_id=session_id))

    while True:
        raw = await asyncio.to_thread(sys.stdin.readline)
        if not raw:
            return
        message = raw.strip()
        if not message:
            continue
        payload = json.loads(message)
        response = await gateway.handle_message(payload)
        if response is None:
            continue
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def run_mcp_stdio_server_sync(app: FastZCP, *, session_id: str = "mcp-stdio") -> None:
    asyncio.run(run_mcp_stdio_server(app, session_id=session_id))
