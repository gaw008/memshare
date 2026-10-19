import asyncio
import json
import socket

import httpx2
import pytest
import pytest_asyncio
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from memhub.server import create_app


@pytest_asyncio.fixture
async def live_server(config):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(create_app(config), host="127.0.0.1", port=port, log_level="error")
    )
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    task.result()
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10)
        sock.close()


def payload(result):
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(next(item.text for item in result.content if item.type == "text"))


@pytest.mark.parametrize("token,identity", [("long-test-token", "claude-code"), ("other-test-token", "gemini-cli")])
async def test_sdk_http_end_to_end_seven_tools_and_authenticated_identity(live_server, config, token, identity):
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as client:
        async with streamable_http_client(f"{live_server}/mcp", http_client=client) as streams:
            async with ClientSession(*streams) as session:
                initialization = await session.initialize()
                assert "get_context" in initialization.instructions
                listed = await session.list_tools()
                assert {tool.name for tool in listed.tools} == {
                    "get_context", "search", "read", "remember", "update", "forget", "log"
                }
                assert all(tool.description for tool in listed.tools)
                context = await session.call_tool("get_context", {})
                assert not context.is_error
                assert "log" in payload(context)["instructions"]
                remembered = await session.call_tool(
                    "remember", {"path": "topics/fitness.md", "fact": "正在做增肌训练", "description": "训练计划"}
                )
                assert not remembered.is_error
                assert payload(remembered)["status"] == "written"
                found = await session.call_tool("search", {"query": "增肌"})
                assert not found.is_error
                assert any("正在做增肌训练" in hit["text"] for hit in payload(found)["hits"])
                read = await session.call_tool("read", {"paths": ["topics/fitness.md"]})
                item = payload(read)["files"][0]
                assert f"[{identity} · " in item["content"]
                assert "version" in item
                logged = await session.call_tool("log", {"entry": "问：怎么预测订单季节性；卡点：不清楚有哪些历史数据"})
                assert not logged.is_error
                journal = (config.memory_dir / payload(logged)["path"]).read_text(encoding="utf-8")
                assert f"卡点：不清楚有哪些历史数据 [{identity} · " in journal
                invalid = await session.call_tool(
                    "remember", {"path": "topics/secrets.md", "fact": "password: synthetic-do-not-save", "description": "说明"}
                )
                assert invalid.is_error
                assert "synthetic-do-not-save" not in str(invalid)
                assert not (config.memory_dir / "topics/secrets.md").exists()


@pytest.mark.parametrize("token", [None, "unknown-token"])
@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
async def test_every_mcp_request_requires_known_token(live_server, method, token):
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    async with httpx2.AsyncClient() as client:
        response = await client.request(method, f"{live_server}/mcp", headers=headers)
    assert response.status_code == 401


async def test_health_endpoint_is_available_without_token(live_server):
    async with httpx2.AsyncClient() as client:
        response = await client.get(f"{live_server}/healthz")
    assert response.status_code == 200
