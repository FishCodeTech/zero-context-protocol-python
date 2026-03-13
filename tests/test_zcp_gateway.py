import asyncio

from zcp import FastZCP, MCPGatewayClient, MCPGatewayServer, stdio_client, stdio_server


def build_gateway_stack():
    app = FastZCP("Gateway Test")

    @app.tool(
        name="weather.get_current",
        description="Get current weather.",
        input_schema={
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
            "additionalProperties": False,
        },
        output_mode="scalar",
        inline_ok=True,
    )
    def get_weather(city: str):
        return {"city": city, "temperature": 24}

    @app.resource("weather://cities", name="Cities", mime_type="application/json")
    def cities():
        return ["Hangzhou", "Beijing"]

    server = stdio_server(app)
    client = stdio_client(server)
    return MCPGatewayServer(server), MCPGatewayClient(client)


def test_mcp_gateway_server_and_client_roundtrip() -> None:
    gateway_server, gateway_client = build_gateway_stack()

    async def run():
        tools_client = await gateway_client.list_tools()
        call_client = await gateway_client.call_tool("weather.get_current", {"city": "Hangzhou"})
        tools_server = await gateway_server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        call_server = await gateway_server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "weather.get_current", "arguments": {"city": "Hangzhou"}},
            }
        )
        resource_server = await gateway_server.handle_message(
            {"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": {"uri": "weather://cities"}}
        )
        return tools_client, call_client, tools_server, call_server, resource_server

    tools_client, call_client, tools_server, call_server, resource_server = asyncio.run(run())

    assert tools_client["tools"][0]["name"] == "weather.get_current"
    assert call_client["structuredContent"] == {"city": "Hangzhou", "temperature": 24}
    assert tools_server["result"]["tools"][0]["name"] == "weather.get_current"
    assert call_server["result"]["structuredContent"] == {"city": "Hangzhou", "temperature": 24}
    assert resource_server["result"]["contents"][0]["uri"] == "weather://cities"
