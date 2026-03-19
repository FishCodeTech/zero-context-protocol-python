import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlsplit

from zcp import (
    AuthProfile,
    BearerAuthConfig,
    FastZCP,
    OAuthConfig,
    PromptArgument,
    SQLiteOAuthProvider,
    ToolExposureConfig,
    ZCPServerConfig,
    create_asgi_app,
)
from zcp.auth import pkce_s256_challenge


def build_http_app(*, oauth_provider=None):
    app = FastZCP(
        "HTTP Test",
        auth_profile=AuthProfile(
            issuer="http://127.0.0.1:8000",
            authorization_url="http://127.0.0.1:8000/authorize",
            token_url="http://127.0.0.1:8000/token",
            scopes=["weather.read"],
        ),
    )

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
        metadata={"groups": ["workflow", "weather"], "stages": ["operate"]},
        required_scopes=("weather.admin",),
    )
    def secure_weather(city: str):
        return {"city": city, "temperature": 24}

    @app.tool(
        name="weather.lookup",
        title="Weather Lookup",
        description="Compatibility weather tool.",
        input_schema={
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "temperature": {"type": "integer"},
                "condition": {"type": "string"},
            },
            "required": ["city", "temperature", "condition"],
        },
        output_mode="scalar",
        inline_ok=True,
        execution={"taskSupport": "optional"},
        metadata={"groups": ["inspection"], "stages": ["verify"]},
    )
    def weather_lookup(city: str):
        return {"city": city, "temperature": 24, "condition": "Cloudy"}

    @app.resource("weather://cities", name="Cities", mime_type="application/json")
    def cities():
        return ["Hangzhou", "Beijing", "Shanghai"]

    @app.resource_template("weather://city/{name}", name="City Weather", mime_type="application/json")
    def city_weather(uri: str):
        return {"uri": uri, "temperature": 24}

    @app.prompt(
        name="weather.summary",
        description="Weather summary prompt.",
        arguments=[PromptArgument(name="city", required=True)],
    )
    def weather_prompt(city: str):
        return [{"role": "user", "content": f"Summarize weather for {city}"}]

    @app.completion("weather.summary")
    def city_completion(request):
        cities = ["Hangzhou", "Beijing", "Shanghai"]
        return [item for item in cities if item.lower().startswith(request.value.lower())]

    asgi = create_asgi_app(
        app,
        config=ZCPServerConfig(
            service_name="http-test",
            auth=BearerAuthConfig(token="secret"),
            tool_exposure=ToolExposureConfig(default_profile="semantic-workflow"),
            oauth=OAuthConfig(enabled=True, issuer="http://127.0.0.1:8000"),
            oauth_provider=oauth_provider,
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


def extract_headers(sent):
    return dict(next(item["headers"] for item in sent if item["type"] == "http.response.start"))


def extract_sse_messages(sent):
    body = b"".join(item.get("body", b"") for item in sent if item["type"] == "http.response.body")
    events = []
    for chunk in body.decode("utf-8").split("\n\n"):
        if not chunk.strip():
            continue
        event = {}
        for line in chunk.splitlines():
            if line.startswith(":"):
                continue
            key, _, value = line.partition(":")
            event[key] = value.lstrip()
        if event:
            events.append(event)
    return events


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
    assert extract_json(metadata)["toolExposure"]["defaultProfile"] == "semantic-workflow"
    assert extract_json(rpc)["result"]["server_info"]["name"] == "HTTP Test"
    assert extract_headers(rpc)[b"mcp-session-id"] == b"s1"
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
        templates = await invoke_http(
            app,
            "POST",
            "/mcp",
            headers=headers,
            body=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "resources/templates/list",
                    "params": {},
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
                    "id": 6,
                    "method": "prompts/get",
                    "params": {"name": "weather.summary", "arguments": {"city": "Hangzhou"}},
                }
            ).encode("utf-8"),
        )
        completion = await invoke_http(
            app,
            "POST",
            "/mcp",
            headers=headers,
            body=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "completion/complete",
                    "params": {
                        "ref": {"type": "ref/prompt", "name": "weather.summary"},
                        "argument": {"name": "city", "value": "Ha"},
                    },
                }
            ).encode("utf-8"),
        )
        return initialize, tools, tool_call, resource, templates, prompt, completion

    initialize, tools, tool_call, resource, templates, prompt, completion = asyncio.run(run())
    initialize_payload = extract_json(initialize)
    tools_payload = extract_json(tools)
    tool_call_payload = extract_json(tool_call)
    resource_payload = extract_json(resource)
    templates_payload = extract_json(templates)
    prompt_payload = extract_json(prompt)
    completion_payload = extract_json(completion)

    assert extract_status(initialize) == 200
    assert initialize_payload["result"]["protocolVersion"] == "2025-11-25"
    assert initialize_payload["result"]["serverInfo"]["name"] == "HTTP Test"
    assert extract_headers(initialize)[b"mcp-session-id"] == b"mcp-http"
    assert tools_payload["result"]["tools"][0]["name"] == "secure.weather"
    assert "inputSchema" in tools_payload["result"]["tools"][0]
    assert "outputSchema" in tools_payload["result"]["tools"][1]
    assert tool_call_payload["result"]["structuredContent"]["city"] == "Hangzhou"
    assert tool_call_payload["result"]["isError"] is False
    assert resource_payload["result"]["contents"][0]["uri"] == "weather://cities"
    assert "Hangzhou" in resource_payload["result"]["contents"][0]["text"]
    assert templates_payload["result"]["resourceTemplates"][0]["uriTemplate"] == "weather://city/{name}"
    assert prompt_payload["result"]["messages"][0]["content"]["type"] == "text"
    assert prompt_payload["result"]["messages"][0]["content"]["text"] == "Summarize weather for Hangzhou"
    assert completion_payload["result"]["completion"]["values"] == ["Hangzhou"]


def test_tool_exposure_default_profile_is_native_only_by_surface() -> None:
    app = build_http_app()
    native_headers = [
        (b"authorization", b"Bearer secret"),
        (b"x-zcp-session", b"native-surface"),
    ]
    mcp_headers = [
        (b"authorization", b"Bearer secret"),
        (b"x-zcp-session", b"mcp-surface"),
    ]

    async def run():
        native = await invoke_http(
            app,
            "POST",
            "/zcp",
            headers=native_headers,
            body=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).encode("utf-8"),
        )
        mcp = await invoke_http(
            app,
            "POST",
            "/mcp",
            headers=mcp_headers,
            body=json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}).encode("utf-8"),
        )
        return native, mcp

    native, mcp = asyncio.run(run())
    native_payload = extract_json(native)
    mcp_payload = extract_json(mcp)

    assert [tool["name"] for tool in native_payload["result"]["tools"]] == ["secure.weather"]
    assert native_payload["result"]["profile"] == "semantic-workflow"
    assert [tool["name"] for tool in mcp_payload["result"]["tools"]] == ["secure.weather", "weather.lookup"]


def test_streamable_http_sse_and_replay() -> None:
    app = build_http_app()
    app.config.sse.keepalive_seconds = 1
    headers = [
        (b"authorization", b"Bearer secret"),
        (b"x-zcp-session", b"stream-http"),
        (b"accept", b"application/json, text/event-stream"),
    ]

    async def run():
        initialize = await invoke_http(
            app,
            "POST",
            "/mcp",
            headers=headers,
            body=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}).encode("utf-8"),
        )
        streamed = await invoke_http(
            app,
            "POST",
            "/mcp",
            headers=headers,
            body=json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}).encode("utf-8"),
        )
        app._sessions["stream-http"].session.emit_log("info", "first-event")
        app._publish_notifications(app._sessions["stream-http"])
        first_stream = await invoke_http(app, "GET", "/mcp", headers=headers)
        first_events = extract_sse_messages(first_stream)
        last_id = next(event["id"] for event in first_events if event.get("data") and "first-event" in event["data"])
        app._sessions["stream-http"].session.emit_log("info", "second-event")
        app._publish_notifications(app._sessions["stream-http"])
        replay = await invoke_http(
            app,
            "GET",
            "/mcp",
            headers=headers + [(b"last-event-id", last_id.encode("latin1"))],
        )
        return initialize, streamed, first_stream, replay

    initialize, streamed, first_stream, replay = asyncio.run(run())
    assert extract_status(initialize) == 200
    assert extract_headers(streamed)[b"content-type"] == b"text/event-stream"
    streamed_events = extract_sse_messages(streamed)
    assert any('"tools"' in event.get("data", "") for event in streamed_events)
    first_events = extract_sse_messages(first_stream)
    assert any("first-event" in event.get("data", "") for event in first_events)
    replay_events = extract_sse_messages(replay)
    assert any("second-event" in event.get("data", "") for event in replay_events)
    assert all("first-event" not in event.get("data", "") for event in replay_events if event.get("data"))


def test_oauth_pkce_flow_and_bearer_access() -> None:
    app = build_http_app()

    verifier = "zcp-pkce-verifier"

    async def run():
        metadata = await invoke_http(app, "GET", "/.well-known/oauth-authorization-server")
        protected = await invoke_http(app, "GET", "/.well-known/oauth-protected-resource/mcp")
        return metadata, protected

    metadata, protected = asyncio.run(run())
    metadata_payload = extract_json(metadata)
    protected_payload = extract_json(protected)
    assert metadata_payload["authorization_endpoint"].endswith("/authorize")
    assert protected_payload["resource"].endswith("/mcp")

    async def authorize_and_exchange():
        code_challenge = pkce_s256_challenge(verifier)
        scope = "response_type=code&client_id=zcp-local-client&redirect_uri=http%3A%2F%2Flocalhost%2Fcallback&state=xyz&scope=weather.read&code_challenge=" + code_challenge + "&code_challenge_method=S256"
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        await app(
            {
                "type": "http",
                "method": "GET",
                "path": "/authorize",
                "query_string": scope.encode("utf-8"),
                "headers": [],
                "client": ("127.0.0.1", 9002),
            },
            receive,
            send,
        )
        headers = dict(next(item["headers"] for item in sent if item["type"] == "http.response.start"))
        location = headers[b"location"].decode("latin1")
        query = parse_qs(urlsplit(location).query)
        code = query["code"][0]
        token = await invoke_http(
            app,
            "POST",
            "/token",
            headers=[(b"content-type", b"application/x-www-form-urlencoded")],
            body=f"grant_type=authorization_code&client_id=zcp-local-client&redirect_uri=http%3A%2F%2Flocalhost%2Fcallback&code={code}&code_verifier={verifier}".encode("utf-8"),
        )
        token_payload = extract_json(token)
        access_token = token_payload["access_token"]
        refresh = await invoke_http(
            app,
            "POST",
            "/token",
            headers=[(b"content-type", b"application/x-www-form-urlencoded")],
            body=f"grant_type=refresh_token&client_id=zcp-local-client&refresh_token={token_payload['refresh_token']}".encode("utf-8"),
        )
        call = await invoke_http(
            app,
            "POST",
            "/mcp",
            headers=[(b"authorization", f"Bearer {access_token}".encode("latin1"))],
            body=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}).encode("utf-8"),
        )
        return token, refresh, call

    token, refresh, call = asyncio.run(authorize_and_exchange())
    assert extract_status(token) == 200
    assert extract_status(refresh) == 200
    assert extract_json(refresh)["access_token"]
    assert extract_status(call) == 200
    assert extract_json(call)["result"]["serverInfo"]["name"] == "HTTP Test"


def test_sqlite_oauth_provider_persists_tokens_across_app_instances() -> None:
    verifier = "sqlite-zcp-verifier"
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "oauth.sqlite3"
        provider = SQLiteOAuthProvider(db_path)
        app = build_http_app(oauth_provider=provider)

        async def issue_token():
            code_challenge = pkce_s256_challenge(verifier)
            scope = "response_type=code&client_id=zcp-local-client&redirect_uri=http%3A%2F%2Flocalhost%2Fcallback&state=sqlite&scope=weather.read&code_challenge=" + code_challenge + "&code_challenge_method=S256"
            sent = []

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message):
                sent.append(message)

            await app(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/authorize",
                    "query_string": scope.encode("utf-8"),
                    "headers": [],
                    "client": ("127.0.0.1", 9100),
                },
                receive,
                send,
            )
            headers = dict(next(item["headers"] for item in sent if item["type"] == "http.response.start"))
            location = headers[b"location"].decode("latin1")
            code = parse_qs(urlsplit(location).query)["code"][0]
            token = await invoke_http(
                app,
                "POST",
                "/token",
                headers=[(b"content-type", b"application/x-www-form-urlencoded")],
                body=f"grant_type=authorization_code&client_id=zcp-local-client&redirect_uri=http%3A%2F%2Flocalhost%2Fcallback&code={code}&code_verifier={verifier}".encode("utf-8"),
            )
            return extract_json(token)["access_token"]

        access_token = asyncio.run(issue_token())

        second_app = build_http_app(oauth_provider=SQLiteOAuthProvider(db_path))

        async def verify_token():
            return await invoke_http(
                second_app,
                "POST",
                "/mcp",
                headers=[(b"authorization", f"Bearer {access_token}".encode("latin1"))],
                body=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}).encode("utf-8"),
            )

        call = asyncio.run(verify_token())
        assert extract_status(call) == 200
        assert extract_json(call)["result"]["serverInfo"]["name"] == "HTTP Test"


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
