import asyncio

from zcp import FastZCP, PromptArgument, sse_client, sse_server, stdio_client, stdio_server, streamable_http_client, streamable_http_server


def build_app() -> FastZCP:
    app = FastZCP("Test ZCP")

    @app.tool(
        name="weather.get_current",
        description="Get weather.",
        input_schema={
            "type": "object",
            "properties": {
                "city": {"type": "string"},
            },
            "required": ["city"],
            "additionalProperties": False,
        },
        output_mode="scalar",
        inline_ok=True,
    )
    def get_weather(city: str):
        return {"city": city, "temperature": 24}

    @app.resource("weather://cities", name="Cities", mime_type="application/json", subscribe=True)
    def cities():
        return ["Hangzhou", "Beijing"]

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

    @app.completion("city")
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
        elicitation_handler=lambda request: {"status": "submitted", "data": {"approved": True, "kind": request["kind"]}},
        log_handler=logs.append,
    )

    async def run():
        init = await client.initialize()
        await client.initialized()
        ping = await client.ping()
        tools = await client.list_tools()
        tool_result = await client.call_tool("weather.get_current", {"city": "Hangzhou"})
        resources = await client.list_resources()
        read_resource = await client.read_resource("weather://cities")
        read_template = await client.read_resource("weather://city/hangzhou")
        await client.subscribe_resource("weather://cities")
        server.emit_resource_updated("weather://cities")
        prompts = await client.list_prompts()
        prompt = await client.get_prompt("weather.summary", {"city": "Hangzhou"})
        completion = await client.complete("city", "city", "Ha")
        await client.set_logging_level("debug")
        server.emit_log("info", "hello")
        roots = await client.list_roots()
        sampling = await client.create_message([{"role": "user", "content": "hello"}])
        elicitation = await client.elicit("form", "Approve?", fields=[{"name": "approved", "type": "boolean", "label": "Approved"}])
        task = await client.create_task("weather.refresh", {"city": "Hangzhou"})
        tasks = await client.list_tasks()
        task_get = await client.get_task(task["task"]["taskId"])
        task_cancel = await client.cancel_task(task["task"]["taskId"])
        return {
            "init": init,
            "ping": ping,
            "tools": tools,
            "tool_result": tool_result,
            "resources": resources,
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
            "task_cancel": task_cancel,
        }

    result = asyncio.run(run())

    assert result["init"]["protocol_version"] == "2025-11-25"
    assert result["ping"]["ok"] is True
    assert result["tools"]["tools"][0]["name"] == "weather.get_current"
    assert result["tool_result"]["content"] == {"city": "Hangzhou", "temperature": 24}
    assert result["resources"]["resources"][0]["uri"] == "weather://cities"
    assert result["read_resource"]["contents"] == ["Hangzhou", "Beijing"]
    assert result["read_template"]["uri"] == "weather://city/hangzhou"
    assert any(note["method"] == "notifications/resources/updated" for note in client.notifications)
    assert result["prompts"]["prompts"][0]["name"] == "weather.summary"
    assert result["prompt"]["messages"][0]["content"] == "Summarize weather for Hangzhou"
    assert result["completion"]["completion"]["values"] == ["Hangzhou"]
    assert any(note["method"] == "notifications/logging/message" for note in client.notifications)
    assert logs[-1]["data"]["message"] == "hello"
    assert result["roots"]["roots"][0]["uri"] == "file:///workspace"
    assert result["sampling"]["message"]["content"] == "sampled:hello"
    assert result["elicitation"]["status"] == "submitted"
    assert result["task"]["task"]["status"] == "completed"
    assert result["tasks"]["tasks"][0]["kind"] == "weather.refresh"
    assert result["task_get"]["task"]["result"]["refreshed"] == "Hangzhou"
    assert result["task_cancel"]["task"]["status"] == "completed"


def test_transport_helpers_return_working_clients() -> None:
    app = build_app()
    sse = sse_client(sse_server(app))
    http = streamable_http_client(streamable_http_server(app))

    async def run():
        return await sse.ping(), await http.ping()

    sse_ping, http_ping = asyncio.run(run())
    assert sse_ping["ok"] is True
    assert http_ping["ok"] is True
