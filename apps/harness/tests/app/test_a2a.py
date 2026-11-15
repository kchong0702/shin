import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, nullcontext
from pathlib import Path

import pytest
from dependency_injector import providers
from fastapi.testclient import TestClient
from httpx import Response
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool, ToolException, tool
from langgraph.graph.state import CompiledStateGraph

from src.agents import Agent, build_agent
from src.app.a2a import Executable
from src.app.app import create_app
from src.app.container import Container
from src.core.config import AgentSettings
from tests.conftest import ScriptedChatModel

PATH = "/zumi/a2a/greeter"
GREETER = AgentSettings(
    name="greeter",
    path=Path(__file__).parents[1] / "agents/fixtures/greeter/profile.yaml",
    family="openai",
    model="gpt-6-luna",
)


@tool
def look_up_name(person: str) -> str:
    """Looks up a person's first name"""
    return "Ada"


def _no_title(person: str) -> str:
    raise ToolException("No title on record")


look_up_title = StructuredTool.from_function(
    _no_title,
    name="look_up_title",
    description="Looks up a person's title",
    handle_tool_error=True,
)


@tool
def look_up_age(person: str) -> str:
    """Looks up a person's age"""
    raise RuntimeError("Registry is down")


def _call(name: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": {"person": "Lovelace"}, "id": f"c-{name}"}],
    )


@asynccontextmanager
async def _in_task_group(
    graph: CompiledStateGraph,
) -> AsyncIterator[CompiledStateGraph]:
    """Opens graph inside two task groups, which wrap the run's errors in nested
    exception groups, as an MCP session does"""
    async with asyncio.TaskGroup(), asyncio.TaskGroup():
        yield graph


def _client(
    model: ScriptedChatModel,
    open_graph: Callable[
        [CompiledStateGraph], AbstractAsyncContextManager[CompiledStateGraph]
    ] = nullcontext,
) -> TestClient:
    container = Container()
    agent = build_agent(
        Agent.load(GREETER),
        tools=(look_up_name, look_up_title, look_up_age),
        model=model,
        checkpointer=container.checkpointer(),
    ).with_config(recursion_limit=15)
    container.executables.override(
        providers.List(
            providers.Object(
                Executable(
                    name="greeter",
                    description="Greets whoever writes to it",
                    open_graph=lambda: open_graph(agent),
                )
            )
        )
    )
    return TestClient(create_app(container))


def _rpc(client: TestClient, method: str, params: dict) -> Response:
    return client.post(
        PATH,
        headers={"A2A-Version": "1.0"},
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
    )


def _message(text: str, context: str | None = None) -> dict:
    return {
        "message": {
            "messageId": f"m-{text}",
            "role": "ROLE_USER",
            "parts": [{"text": text}],
            **({"contextId": context} if context else {}),
        }
    }


def _say(client: TestClient, text: str, context: str | None = None) -> dict:
    return _rpc(client, "SendMessage", _message(text, context)).json()["result"]["task"]


def _stream(client: TestClient, text: str) -> list[dict]:
    response = _rpc(client, "SendStreamingMessage", _message(text))
    return [
        json.loads(line.removeprefix("data:"))["result"]
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]


def _steps(events: list[dict]) -> list[dict]:
    return [
        part["data"]["step"]
        for event in events
        if "statusUpdate" in event
        for part in event["statusUpdate"]["status"].get("message", {}).get("parts", [])
        if "step" in part.get("data", {})
    ]


def test_card_points_clients_at_the_agent():
    card = _client(ScriptedChatModel(replies=[AIMessage("Hi")])).get(
        f"{PATH}/.well-known/agent-card.json"
    )

    assert card.json()["name"] == "greeter"
    assert card.json()["supportedInterfaces"][0]["url"] == (
        f"http://127.0.0.1:8300{PATH}"
    )


def test_an_agent_takes_the_users_text_and_replies_in_text():
    model = ScriptedChatModel(replies=[AIMessage("Hello, Ada!")])

    task = _say(_client(model), "Hi, I'm Ada")

    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    [reply] = [a for a in task["artifacts"] if a["name"] == "greeter"]
    assert reply["parts"][0]["text"] == "Hello, Ada!"
    assert model.requests[0][-1].text == "Hi, I'm Ada"


def test_an_agent_remembers_earlier_turns_in_the_same_context():
    model = ScriptedChatModel(
        replies=[AIMessage("Hello, Ada!"), AIMessage("Your name is Ada.")]
    )
    client = _client(model)

    first = _say(client, "Hi, I'm Ada")
    _say(client, "What's my name?", context=first["contextId"])

    assert [m.text for m in model.requests[1][-3:]] == [
        "Hi, I'm Ada",
        "Hello, Ada!",
        "What's my name?",
    ]


def test_a_run_streams_each_tool_call_as_a_step_and_the_model_text_as_an_artifact():
    model = ScriptedChatModel(replies=[_call("look_up_name"), AIMessage("Hello, Ada!")])

    events = _stream(_client(model), "Greet Lovelace")

    assert _steps(events) == [
        {"id": "c-look_up_name", "name": "look_up_name", "status": "running"},
        {"id": "c-look_up_name", "name": "look_up_name", "status": "done"},
    ]
    streamed = [
        part["text"]
        for event in events
        if "artifactUpdate" in event
        and event["artifactUpdate"]["artifact"]["name"] == "text"
        for part in event["artifactUpdate"]["artifact"]["parts"]
    ]
    assert streamed == ["Hello, Ada!"]
    assert events[-1]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"


def test_a_tool_error_the_model_sees_is_a_failed_step_and_the_run_goes_on():
    model = ScriptedChatModel(replies=[_call("look_up_title"), AIMessage("No title.")])

    events = _stream(_client(model), "Greet Lovelace")

    assert [step["status"] for step in _steps(events)] == ["running", "failed"]
    assert events[-1]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"


@pytest.mark.parametrize("open_graph", [nullcontext, _in_task_group])
def test_a_tool_that_raises_fails_the_task_with_its_reason(open_graph):
    model = ScriptedChatModel(replies=[_call("look_up_age")])

    task = _say(_client(model, open_graph), "Greet Lovelace")

    assert task["status"]["state"] == "TASK_STATE_FAILED"
    assert "Registry is down" in task["status"]["message"]["parts"][0]["text"]


@pytest.mark.parametrize("open_graph", [nullcontext, _in_task_group])
def test_an_agent_that_never_answers_fails_the_task_at_the_recursion_limit(open_graph):
    model = ScriptedChatModel(replies=[_call("look_up_name")])

    task = _say(_client(model, open_graph), "Greet Lovelace")

    assert task["status"]["state"] == "TASK_STATE_FAILED"
    assert task["status"]["message"]["parts"][0]["text"] == (
        "Ran out of steps before finishing"
    )
