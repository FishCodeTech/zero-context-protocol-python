import asyncio

from zcp import FastZCP, PromptArgument, sse_client, sse_server, stdio_client, stdio_server, streamable_http_client, streamable_http_server


def build_app() -> FastZCP:
    app = FastZCP("Test ZCP", title="Test ZCP Server", description="Test server for SDK coverage")

    @app.tool(
        name="weather.get_current",
        title="Current Weather",
        description="Get weather.",
        input_schema={
            "type": "object",
            "properties": {
                "city": {"type": "string"},
            },
            "required": ["city"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "temperature": {"type": "integer"},
            },
            "required": ["city", "temperature"],
        },
        output_mode="scalar",
        inline_ok=True,
        annotations={"title": "Current Weather", "readOnlyHint": True},
        execution={"taskSupport": "optional"},
        metadata={"groups": ["workflow", "weather"], "stages": ["operate"]},
    )
    def get_weather(city: str, ctx):
        return {"city": city, "temperature": 24}

    @app.tool(
        name="weather.debug_lookup",
        description="Debug weather lookup.",
        input_schema={
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
            "additionalProperties": False,
        },
        output_mode="scalar",
        inline_ok=True,
        metadata={"groups": ["inspection"], "stages": ["verify"]},
    )
    def debug_lookup(city: str):
        return {"city": city, "debug": True}

    @app.resource(
        "weather://cities",
        name="Cities",
        title="City List",
        mime_type="application/json",
        subscribe=True,
        annotations={"audience": ["assistant"]},
    )
    def cities():
        return ["Hangzhou", "Beijing"]

    @app.resource_template("weather://city/{name}", name="City Weather", title="City Weather", mime_type="application/json")
    def city_weather(uri: str):
        return {"uri": uri, "temperature": 24}

    @app.prompt(
        name="weather.summary",
        title="Weather Summary",
        description="Weather summary prompt.",
        arguments=[PromptArgument(name="city", required=True)],
    )
    def weather_prompt(city: str):
        return [{"role": "user", "content": f"Summarize weather for {city}"}]

    @app.completion("weather.summary")
    def city_completion(request):
        cities = ["Hangzhou", "Beijing", "Shanghai"]
        return [item for item in cities if item.lower().startswith(request.value.lower())]

    @app.task("weather.refresh")
    def refresh_task(payload):
        return {"refreshed": payload["city"]}

    return app


def test_zcp_sdk_features_end_to_end() -> None:
    app = build_app()
    server = stdio_server(app)
    logs = []
    client = stdio_client(
        server,
        roots_provider=lambda: [{"uri": "file:///workspace", "name": "workspace"}],
        sampling_handler=lambda request: {"message": {"role": "assistant", "content": f"sampled:{request.messages[-1]['content']}"}},
        elicitation_handler=lambda request: {"action": "accept", "content": {"approved": True, "mode": request.get("mode", request.get("kind"))}},
        log_handler=logs.append,
    )

    async def run():
        init = await client.initialize()
        await client.initialized()
        ping = await client.ping()
        tools = await client.list_tools()
        tool_result = await client.call_tool("weather.get_current", {"city": "Hangzhou"}, meta={"progressToken": "p1"})
        resources = await client.list_resources()
        templates = await client.list_resource_templates()
        read_resource = await client.read_resource("weather://cities")
        read_template = await client.read_resource("weather://city/hangzhou")
        await client.subscribe_resource("weather://cities")
        server.emit_resource_updated("weather://cities")
        prompts = await client.list_prompts()
        prompt = await client.get_prompt("weather.summary", {"city": "Hangzhou"})
        completion = await client.complete(
            {"type": "ref/prompt", "name": "weather.summary"},
            {"name": "city", "value": "Ha"},
            context_arguments={"country": "CN"},
        )
        await client.set_logging_level("debug")
        server.emit_log("info", "hello")
        roots = await client.list_roots()
        sampling = await client.create_message([{"role": "user", "content": "hello"}])
        elicitation = await client.elicit("form", "Approve?", fields=[{"name": "approved", "type": "boolean", "label": "Approved"}])
        task = await client.create_task("weather.refresh", {"city": "Hangzhou"}, task={"ttl": 5000, "pollInterval": 200})
        await asyncio.sleep(0)
        tasks = await client.list_tasks()
        task_get = await client.get_task(task["task"]["taskId"])
        task_result = await client.get_task_result(task["task"]["taskId"])
        task_cancel = await client.cancel_task(task["task"]["taskId"])
        tool_task = await client.call_tool_as_task("weather.get_current", {"city": "Hangzhou"}, ttl=5000)
        await asyncio.sleep(0)
        tool_task_get = await client.get_task(tool_task["task"]["taskId"])
        tool_task_result = await client.get_task_result(tool_task["task"]["taskId"])
        return {
            "init": init,
            "ping": ping,
            "tools": tools,
            "tool_result": tool_result,
            "resources": resources,
            "templates": templates,
            "read_resource": read_resource,
            "read_template": read_template,
            "prompts": prompts,
            "prompt": prompt,
            "completion": completion,
            "roots": roots,
            "sampling": sampling,
            "elicitation": elicitation,
            "task": task,
            "tasks": tasks,
            "task_get": task_get,
            "task_result": task_result,
            "task_cancel": task_cancel,
            "tool_task": tool_task,
            "tool_task_get": tool_task_get,
            "tool_task_result": tool_task_result,
        }

    result = asyncio.run(run())

    assert result["init"]["protocol_version"] == "2025-11-25"
    assert result["init"]["server_info"]["title"] == "Test ZCP Server"
    assert result["ping"]["ok"] is True
    assert result["tools"]["tools"][0]["name"] == "weather.get_current"
    assert result["tools"]["tools"][0]["outputSchema"]["properties"]["city"]["type"] == "string"
    assert result["tool_result"]["structuredContent"]["city"] == "Hangzhou"
    assert result["resources"]["resources"][0]["uri"] == "weather://cities"
    assert result["templates"]["resourceTemplates"][0]["uriTemplate"] == "weather://city/{name}"
    assert result["read_resource"]["contents"] == ["Hangzhou", "Beijing"]
    assert result["read_template"]["uri"] == "weather://city/hangzhou"
    assert any(note["method"] == "notifications/resources/updated" for note in client.notifications)
    assert result["prompts"]["prompts"][0]["name"] == "weather.summary"
    assert result["prompt"]["messages"][0]["content"] == "Summarize weather for Hangzhou"
    assert result["completion"]["completion"]["values"] == ["Hangzhou"]
    assert any(note["method"] == "notifications/message" for note in client.notifications)
    assert logs[-1]["data"] == "hello"
    assert result["roots"]["roots"][0]["uri"] == "file:///workspace"
    assert result["sampling"]["message"]["content"] == "sampled:hello"
    assert result["elicitation"]["action"] == "accept"
    assert result["task"]["task"]["status"] in {"queued", "working", "completed"}
    assert result["tasks"]["tasks"][0]["status"] == "completed"
    assert result["task_get"]["task"]["taskId"] == result["task"]["task"]["taskId"]
    assert result["task_result"]["refreshed"] == "Hangzhou"
    assert result["task_cancel"]["task"]["status"] == "completed"
    assert result["tool_task"]["task"]["kind"] == "tool:weather.get_current"
    assert result["tool_task_get"]["task"]["status"] == "completed"
    assert result["tool_task_result"]["structuredContent"]["city"] == "Hangzhou"


def test_transport_helpers_return_working_clients() -> None:
    app = build_app()
    sse = sse_client(sse_server(app))
    http = streamable_http_client(streamable_http_server(app))

    async def run():
        return await sse.ping(), await http.ping()

    sse_ping, http_ping = asyncio.run(run())
    assert sse_ping["ok"] is True
    assert http_ping["ok"] is True


def test_list_tools_supports_semantic_workflow_profile() -> None:
    app = build_app()
    client = stdio_client(stdio_server(app))

    async def run():
        await client.initialize()
        await client.initialized()
        return await client.list_tools(profile="semantic-workflow")

    tools = asyncio.run(run())
    names = [tool["name"] for tool in tools["tools"]]
    assert names == ["weather.get_current"]
    assert tools["profile"] == "semantic-workflow"


def test_native_default_profile_enforces_semantic_visibility_for_list_and_call() -> None:
    app = FastZCP("profiled-native", default_tool_profiles={"native": "semantic-workflow"})

    @app.tool(
        name="zcp.semantic_plan",
        description="semantic workflow",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        output_mode="scalar",
        inline_ok=True,
        metadata={"groups": ["workflow"]},
    )
    def semantic_plan():
        return {"ok": True}

    @app.tool(
        name="sheet.write_raw",
        description="primitive write",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        output_mode="scalar",
        inline_ok=True,
        metadata={"groups": ["write"]},
    )
    def write_raw():
        return {"ok": True}

    client = stdio_client(stdio_server(app))

    async def run():
        await client.initialize()
        await client.initialized()
        listed = await client.list_tools()
        semantic_result = await client.call_tool("zcp.semantic_plan", {})
        blocked = None
        try:
            await client.call_tool("sheet.write_raw", {})
        except RuntimeError as exc:
            blocked = str(exc)
        return listed, semantic_result, blocked

    listed, semantic_result, blocked = asyncio.run(run())
    assert [tool["name"] for tool in listed["tools"]] == ["zcp.semantic_plan"]
    assert listed["profile"] == "semantic-workflow"
    assert semantic_result["structuredContent"]["ok"] is True
    assert blocked == "not_found:sheet.write_raw"
