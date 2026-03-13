import asyncio
import json

from zcp import BearerAuthConfig, FastZCP, PromptArgument, ZCPServerConfig, create_asgi_app


def build_http_app():
    app = FastZCP("HTTP Test")

    @app.tool(
        name="secure.weather",
        description="Protected weather tool.",
        input_schema={
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
            "additionalProperties": False,
        },
        output_mode="scalar",
        inline_ok=True,
        required_scopes=("weather.read",),
    )
    def secure_weather(city: str):
        return {"city": city, "temperature": 24}

    @app.tool(
        name="weather.lookup",
        description="Compatibility weather tool.",
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
        return {"city": city, "temperature": 24, "condition": "Cloudy"}

    @app.resource("weather://cities", name="Cities", mime_type="application/json")
    def cities():
        return ["Hangzhou", "Beijing", "Shanghai"]

    @app.prompt(
        name="weather.summary",
        description="Weather summary prompt.",
        arguments=[PromptArgument(name="city", required=True)],
    )
    def weather_prompt(city: str):
        return [{"role": "user", "content": f"Summarize weather for {city}"}]

    asgi = create_asgi_app(
        app,
        config=ZCPServerConfig(
            service_name="http-test",
            auth=BearerAuthConfig(token="secret"),
        ),
    )
    return asgi


async def invoke_http(app, method, path, body=None, headers=None, client=("127.0.0.1", 1234)):
    sent = []
    chunks = [body or b""]

    async def receive():
        chunk = chunks.pop(0) if chunks else b""
        return {"type": "http.request", "body": chunk, "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": headers or [],
        "client": client,
    }
    await app(scope, receive, send)
    return sent


def extract_json(sent):
    body = b"".join(item.get("body", b"") for item in sent if item["type"] == "http.response.body")
    return json.loads(body.decode("utf-8") or "{}")


def extract_status(sent):
    return next(item["status"] for item in sent if item["type"] == "http.response.start")


def test_http_metadata_and_auth_and_rpc() -> None:
    app = build_http_app()

    async def run():
        metadata = await invoke_http(app, "GET", "/metadata")
        rpc = await invoke_http(
            app,
            "POST",
            "/zcp",
            headers=[
                (b"authorization", b"Bearer secret"),
                (b"x-zcp-session", b"s1"),
            ],
            body=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {},
                }
            ).encode("utf-8"),
        )
        docs = await invoke_http(app, "GET", "/docs")
        unauthorized_rpc = await invoke_http(
            app,
            "POST",
            "/zcp",
            body=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 99,
                    "method": "ping",
                    "params": {},
                }
            ).encode("utf-8"),
        )
        forbidden_tool = await invoke_http(
            app,
            "POST",
            "/zcp",
            headers=[
                (b"authorization", b"Bearer secret"),
                (b"x-zcp-session", b"s1"),
            ],
            body=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "secure.weather", "arguments": {"city": "Hangzhou"}},
                }
            ).encode("utf-8"),
        )
        return metadata, docs, rpc, unauthorized_rpc, forbidden_tool

    metadata, docs, rpc, unauthorized_rpc, forbidden_tool = asyncio.run(run())
    assert extract_status(metadata) == 200
    assert extract_status(docs) == 404
    assert extract_json(metadata)["service"] == "http-test"
    assert extract_json(metadata)["http"]["mcpPath"] == "/mcp"
    assert extract_json(rpc)["result"]["server_info"]["name"] == "HTTP Test"
    assert extract_status(unauthorized_rpc) == 401
    assert extract_json(forbidden_tool)["error"]["message"].startswith("forbidden:missing_scopes")


def test_http_mcp_surface_shapes() -> None:
    app = build_http_app()
    headers = [
        (b"authorization", b"Bearer secret"),
        (b"x-zcp-session", b"mcp-http"),
    ]

    async def run():
        initialize = await invoke_http(
            app,
            "POST",
            "/mcp",
            headers=headers,
            body=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "pytest", "version": "1.0"},
                    },
                }
            ).encode("utf-8"),
        )
        tools = await invoke_http(
            app,
            "POST",
            "/mcp",
            headers=headers,
            body=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                }
            ).encode("utf-8"),
        )
        tool_call = await invoke_http(
            app,
            "POST",
            "/mcp",
            headers=headers,
            body=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "weather.lookup", "arguments": {"city": "Hangzhou"}},
                }
            ).encode("utf-8"),
        )
        resource = await invoke_http(
            app,
            "POST",
            "/mcp",
            headers=headers,
            body=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "resources/read",
                    "params": {"uri": "weather://cities"},
                }
            ).encode("utf-8"),
        )
        prompt = await invoke_http(
            app,
            "POST",
            "/mcp",
            headers=headers,
            body=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "prompts/get",
                    "params": {"name": "weather.summary", "arguments": {"city": "Hangzhou"}},
                }
            ).encode("utf-8"),
        )
        return initialize, tools, tool_call, resource, prompt

    initialize, tools, tool_call, resource, prompt = asyncio.run(run())
    initialize_payload = extract_json(initialize)
    tools_payload = extract_json(tools)
    tool_call_payload = extract_json(tool_call)
    resource_payload = extract_json(resource)
    prompt_payload = extract_json(prompt)

    assert extract_status(initialize) == 200
    assert initialize_payload["result"]["protocolVersion"] == "2025-11-25"
    assert initialize_payload["result"]["serverInfo"]["name"] == "HTTP Test"
    assert tools_payload["result"]["tools"][0]["name"] == "secure.weather"
    assert "inputSchema" in tools_payload["result"]["tools"][0]
    assert tool_call_payload["result"]["structuredContent"]["city"] == "Hangzhou"
    assert tool_call_payload["result"]["isError"] is False
    assert resource_payload["result"]["contents"][0]["uri"] == "weather://cities"
    assert "Hangzhou" in resource_payload["result"]["contents"][0]["text"]
    assert prompt_payload["result"]["messages"][0]["content"]["type"] == "text"
    assert prompt_payload["result"]["messages"][0]["content"]["text"] == "Summarize weather for Hangzhou"


def test_http_rate_limit() -> None:
    app = build_http_app()
    app.config.rate_limit.max_requests = 2
    headers = [(b"authorization", b"Bearer secret")]

    async def run():
        first = await invoke_http(app, "GET", "/healthz", headers=headers, client=("9.9.9.9", 9999))
        second = await invoke_http(app, "GET", "/readyz", headers=headers, client=("9.9.9.9", 9999))
        third = await invoke_http(app, "POST", "/zcp", headers=headers, client=("9.9.9.9", 9999), body=b"{}")
        return first, second, third

    first, second, third = asyncio.run(run())
    assert extract_status(first) == 200
    assert extract_status(second) == 200
    assert extract_status(third) == 429
