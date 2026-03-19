import asyncio
import json

from zcp import BearerAuthConfig, FastZCP, ZCPServerConfig, create_asgi_app


def build_ws_app():
    app = FastZCP("WebSocket Test")

    @app.tool(
        name="weather.lookup",
        description="Lookup weather.",
        input_schema={
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
            "additionalProperties": False,
        },
        output_mode="scalar",
        inline_ok=True,
    )
    def weather_lookup(city: str):
        return {"city": city, "temperature": 24}

    return create_asgi_app(
        app,
        config=ZCPServerConfig(
            service_name="ws-test",
            auth=BearerAuthConfig(token="secret"),
        ),
    )


async def invoke_websocket(app, messages):
    sent = []
    queue = asyncio.Queue()
    for item in messages:
        await queue.put(item)
    await queue.put({"type": "websocket.disconnect"})

    async def receive():
        return await queue.get()

    async def send(message):
        sent.append(message)

    scope = {
        "type": "websocket",
        "path": "/ws",
        "headers": [(b"authorization", b"Bearer secret")],
        "subprotocols": ["mcp"],
        "client": ("127.0.0.1", 9999),
    }
    await app(scope, receive, send)
    return sent


def test_websocket_mcp_surface() -> None:
    app = build_ws_app()

    async def run():
        return await invoke_websocket(
            app,
            [
                {
                    "type": "websocket.receive",
                    "text": json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
                },
                {
                    "type": "websocket.receive",
                    "text": json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "tools/call",
                            "params": {"name": "weather.lookup", "arguments": {"city": "Hangzhou"}},
                        }
                    ),
                },
            ],
        )

    sent = asyncio.run(run())
    assert sent[0]["type"] == "websocket.accept"
    payloads = [json.loads(item["text"]) for item in sent if item["type"] == "websocket.send"]
    assert payloads[0]["result"]["protocolVersion"] == "2025-11-25"
    assert payloads[1]["result"]["structuredContent"]["city"] == "Hangzhou"
