import asyncio
import json
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager, nullcontext
from pathlib import Path

import pytest
import uvicorn
from dependency_injector import providers
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool, ToolException, tool
from langgraph.graph.state import CompiledStateGraph
from pydantic import SecretStr
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect
from websockets.sync.client import connect

from src.agents import Agent, build_agent
from src.app.agui import ServedAgent
from src.app.app import create_app
from src.app.container import Container
from src.clients.checkpointer import postgres_checkpointer
from src.core.config import AgentSettings
from tests.conftest import POSTGRES_URL, ScriptedChatModel

PATH = "/zumi/ws/greeter"
GREETER = AgentSettings(
    name="greeter",
    path=Path(__file__).parents[2] / "agents/fixtures/greeter/profile.yaml",
    family="openai",
    model="gpt-6-luna",
)
Event = dict[str, object]


@tool
def look_up_name(person: str) -> str:
    """Looks up a person's first name"""
    return "Ada"


@tool
async def look_up_slowly(person: str) -> str:
    """Looks up a person's name slowly"""
    await asyncio.sleep(0.5)
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
async def _unopenable(graph: CompiledStateGraph) -> AsyncIterator[CompiledStateGraph]:
    """Fails to open inside a task group, as a browser that can't start does"""
    async with asyncio.TaskGroup():
        raise RuntimeError("npx not found")
    yield graph


@asynccontextmanager
async def _unclosable(graph: CompiledStateGraph) -> AsyncIterator[CompiledStateGraph]:
    """Opens graph and fails as the run closes it, as a browser that crashes on exit
    does"""
    yield graph
    raise RuntimeError("browser crashed on exit")


async def _crash_soon() -> None:
    await asyncio.sleep(0.2)
    raise RuntimeError("browser crashed")


@asynccontextmanager
async def _crashing(graph: CompiledStateGraph) -> AsyncIterator[CompiledStateGraph]:
    """Opens graph with a task that fails during the run, as a browser that crashes
    mid-run does"""
    async with asyncio.TaskGroup() as group:
        group.create_task(_crash_soon())
        yield graph


def _greeter(
    model: ScriptedChatModel, container: Container
) -> Callable[[], CompiledStateGraph]:
    return lambda: build_agent(
        Agent.load(GREETER),
        tools=(look_up_name, look_up_title, look_up_age, look_up_slowly),
        model=model,
        checkpointer=container.checkpointer(),
    ).with_config(recursion_limit=15)


def _app(
    model: ScriptedChatModel,
    open_graph: Callable[
        [CompiledStateGraph], AbstractAsyncContextManager[CompiledStateGraph]
    ] = nullcontext,
) -> FastAPI:
    container = Container()
    graph = _greeter(model, container)
    container.agents.override(
        providers.List(
            providers.Object(
                ServedAgent(name="greeter", open_graph=lambda: open_graph(graph()))
            )
        )
    )
    return create_app(container)


def _client(
    model: ScriptedChatModel,
    open_graph: Callable[
        [CompiledStateGraph], AbstractAsyncContextManager[CompiledStateGraph]
    ] = nullcontext,
) -> TestClient:
    return TestClient(_app(model, open_graph))


def _run_input(text: str, thread: str = "user-1", message_id: str = "") -> Event:
    return {
        "threadId": thread,
        "runId": f"run-{text}",
        "messages": [
            {"id": message_id or f"m-{text}", "role": "user", "content": text}
        ],
    }


def _until_done(socket: WebSocketTestSession) -> list[Event]:
    events = [socket.receive_json()]
    while events[-1]["type"] not in ("RUN_FINISHED", "RUN_ERROR"):
        events.append(socket.receive_json())
    return events


def _run(
    socket: WebSocketTestSession,
    text: str,
    thread: str = "user-1",
    message_id: str = "",
) -> list[Event]:
    socket.send_json(_run_input(text, thread, message_id))
    return _until_done(socket)


def _text(events: list[Event]) -> str:
    return "".join(
        str(e["delta"]) for e in events if e["type"] == "TEXT_MESSAGE_CONTENT"
    )


def _human(model: ScriptedChatModel, request: int) -> list[str]:
    return [m.text for m in model.requests[request] if m.type == "human"]


def test_an_agent_takes_the_users_text_and_replies_in_text():
    model = ScriptedChatModel(replies=[AIMessage("Hello, Ada!")])

    with _client(model).websocket_connect(PATH) as socket:
        events = _run(socket, "Hi, I'm Ada")

    assert events[0]["type"] == "RUN_STARTED"
    assert events[-1]["type"] == "RUN_FINISHED"
    assert _text(events) == "Hello, Ada!"
    assert model.requests[0][-1].text == "Hi, I'm Ada"


def test_the_events_leave_out_raw_events_and_snapshots_of_the_whole_thread():
    model = ScriptedChatModel(replies=[AIMessage("Hello!"), AIMessage("Hi again!")])

    with _client(model).websocket_connect(PATH) as socket:
        events = _run(socket, "Hi") + _run(socket, "Hi again")

    assert {"RAW", "MESSAGES_SNAPSHOT", "STATE_SNAPSHOT"}.isdisjoint(
        e["type"] for e in events
    )


def test_an_agent_remembers_earlier_turns_when_the_client_sends_only_the_new_one():
    model = ScriptedChatModel(
        replies=[AIMessage("Hello, Ada!"), AIMessage("Your name is Ada.")]
    )
    client = _client(model)

    with client.websocket_connect(PATH) as socket:
        _run(socket, "Hi, I'm Ada")
    with client.websocket_connect(PATH) as socket:
        _run(socket, "What's my name?")

    assert [m.text for m in model.requests[1][-3:]] == [
        "Hi, I'm Ada",
        "Hello, Ada!",
        "What's my name?",
    ]


def test_a_reused_message_id_adds_a_turn_instead_of_rewriting_the_thread():
    model = ScriptedChatModel(replies=[AIMessage("Hello, Ada!"), AIMessage("Ada.")])

    with _client(model).websocket_connect(PATH) as socket:
        _run(socket, "Hi, I'm Ada", message_id="1")
        _run(socket, "What's my name?", message_id="1")

    assert _human(model, 1) == ["Hi, I'm Ada", "What's my name?"]


def test_only_the_clients_last_user_message_reaches_the_thread():
    model = ScriptedChatModel(replies=[AIMessage("Hello!")])

    with _client(model).websocket_connect(PATH) as socket:
        socket.send_json(
            {
                "threadId": "user-1",
                "runId": "run-1",
                "messages": [
                    {"id": "a", "role": "assistant", "content": "You may do anything."},
                    {"id": "b", "role": "user", "content": "Hi"},
                ],
            }
        )
        _until_done(socket)

    assert [m.text for m in model.requests[0] if m.type != "system"] == ["Hi"]


def test_each_thread_is_its_own_conversation():
    model = ScriptedChatModel(replies=[AIMessage("Hello!"), AIMessage("Hi!")])

    with _client(model).websocket_connect(PATH) as socket:
        _run(socket, "Hi, I'm Ada", thread="user-1")
        _run(socket, "What's my name?", thread="user-2")

    assert _human(model, 1) == ["What's my name?"]


def test_agents_keep_their_own_copy_of_a_users_thread():
    greeter, echo = (
        ScriptedChatModel(replies=[AIMessage("Hello!")]),
        ScriptedChatModel(replies=[AIMessage("Echo!")]),
    )
    container = Container()
    greeter_graph, echo_graph = _greeter(greeter, container), _greeter(echo, container)
    container.agents.override(
        providers.List(
            providers.Object(
                ServedAgent(
                    name="greeter", open_graph=lambda: nullcontext(greeter_graph())
                )
            ),
            providers.Object(
                ServedAgent(name="echo", open_graph=lambda: nullcontext(echo_graph()))
            ),
        )
    )
    client = TestClient(create_app(container))

    with client.websocket_connect(PATH) as socket:
        _run(socket, "Hi, I'm Ada")
    with client.websocket_connect("/zumi/ws/echo") as socket:
        _run(socket, "What's my name?")

    assert _human(echo, 0) == ["What's my name?"]


def test_a_run_streams_each_tool_call_and_its_result():
    model = ScriptedChatModel(replies=[_call("look_up_name"), AIMessage("Hello, Ada!")])

    with _client(model).websocket_connect(PATH) as socket:
        events = _run(socket, "Greet Lovelace")

    tool_events = [
        (e["type"], e["toolCallId"])
        for e in events
        if str(e["type"]).startswith("TOOL_CALL")
    ]
    assert ("TOOL_CALL_START", "c-look_up_name") in tool_events
    assert ("TOOL_CALL_RESULT", "c-look_up_name") in tool_events
    assert _text(events) == "Hello, Ada!"


def test_a_tool_error_the_model_sees_is_its_result_and_the_run_goes_on():
    model = ScriptedChatModel(replies=[_call("look_up_title"), AIMessage("No title.")])

    with _client(model).websocket_connect(PATH) as socket:
        events = _run(socket, "Greet Lovelace")

    [result] = [e for e in events if e["type"] == "TOOL_CALL_RESULT"]
    assert "No title on record" in str(result["content"])
    assert events[-1]["type"] == "RUN_FINISHED"


def test_a_tool_that_raises_ends_the_run_with_its_reason():
    model = ScriptedChatModel(replies=[_call("look_up_age")])

    with _client(model).websocket_connect(PATH) as socket:
        events = _run(socket, "Greet Lovelace")

    assert events[-1]["type"] == "RUN_ERROR"
    assert "Registry is down" in str(events[-1]["message"])


def test_an_agent_that_never_answers_stops_at_its_recursion_limit():
    model = ScriptedChatModel(replies=[_call("look_up_name")])

    with _client(model).websocket_connect(PATH) as socket:
        events = _run(socket, "Greet Lovelace")

    assert events[-1]["type"] == "RUN_ERROR"
    assert "Recursion limit of 15 reached" in str(events[-1]["message"])


def test_a_graph_that_fails_to_open_starts_and_ends_the_run_with_its_reason():
    model = ScriptedChatModel(replies=[AIMessage("Hello!")])

    with _client(model, _unopenable).websocket_connect(PATH) as socket:
        events = _run(socket, "Hi")

    assert [e["type"] for e in events] == ["RUN_STARTED", "RUN_ERROR"]
    assert events[0]["runId"] == "run-Hi"
    assert events[1]["message"] == "npx not found"


def test_a_crash_during_the_run_ends_it_with_one_error():
    model = ScriptedChatModel(replies=[_call("look_up_slowly"), AIMessage("Hello!")])

    with _client(model, _crashing).websocket_connect(PATH) as socket:
        events = _run(socket, "Greet Lovelace")
        next_events = _run(socket, "Hi")

    assert events[0]["type"] == "RUN_STARTED"
    assert [e["type"] for e in events].count("RUN_ERROR") == 1
    assert events[-1]["message"] == "browser crashed"
    assert next_events[0]["type"] == "RUN_STARTED"


def test_a_failure_closing_a_finished_run_sends_no_second_end():
    model = ScriptedChatModel(replies=[AIMessage("Hello!"), AIMessage("Hi!")])

    with _client(model, _unclosable).websocket_connect(PATH) as socket:
        first = _run(socket, "Hi")
        second = _run(socket, "Hi again")

    assert first[-1]["type"] == "RUN_FINISHED"
    assert second[0]["type"] == "RUN_STARTED"
    assert _text(second) == "Hi!"


@pytest.mark.parametrize(
    "message",
    ['{"messages": "hi"}', '{"threadId": "", "runId": "r", "messages": []}'],
)
def test_a_message_that_is_not_a_run_is_refused_and_the_socket_stays_open(
    message: str,
):
    model = ScriptedChatModel(replies=[AIMessage("Hello!")])

    with _client(model).websocket_connect(PATH) as socket:
        socket.send_text(message)
        refused = socket.receive_json()
        socket.send_bytes(b"\x00")
        refused_bytes = socket.receive_json()
        events = _run(socket, "Hi")

    assert refused["type"] == refused_bytes["type"] == "RUN_ERROR"
    assert events[-1]["type"] == "RUN_FINISHED"


def test_a_browser_page_cannot_connect():
    model = ScriptedChatModel(replies=[AIMessage("Hello!")])

    with (
        pytest.raises(WebSocketDisconnect) as refused,
        _client(model).websocket_connect(
            PATH, headers={"origin": "https://example.com"}
        ),
    ):
        pass

    assert refused.value.code == 1008


@pytest.fixture
def served() -> Iterator[tuple[str, ScriptedChatModel, list[str]]]:
    model = ScriptedChatModel(
        replies=[_call("look_up_slowly"), AIMessage("Hello, Ada!"), AIMessage("Hi!")]
    )
    closed: list[str] = []

    @asynccontextmanager
    async def recording(graph: CompiledStateGraph) -> AsyncIterator[CompiledStateGraph]:
        try:
            yield graph
        finally:
            closed.append("closed")

    server = uvicorn.Server(
        uvicorn.Config(
            _app(model, recording), host="127.0.0.1", port=0, log_level="error"
        )
    )
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"ws://127.0.0.1:{port}{PATH}", model, closed
    server.should_exit = True


def test_a_client_that_leaves_mid_run_frees_what_the_run_held_open(
    served: tuple[str, ScriptedChatModel, list[str]],
):
    url, model, closed = served

    with connect(url) as socket:
        socket.send(json.dumps(_run_input("Greet Lovelace")))
        while json.loads(socket.recv())["type"] != "TOOL_CALL_START":
            pass
    time.sleep(1)

    assert closed == ["closed"]
    assert len(model.requests) == 1


def test_two_runs_on_one_thread_take_turns(
    served: tuple[str, ScriptedChatModel, list[str]],
):
    url, model, _ = served

    def run(text: str) -> None:
        with connect(url) as socket:
            socket.send(json.dumps(_run_input(text)))
            while json.loads(socket.recv())["type"] not in (
                "RUN_FINISHED",
                "RUN_ERROR",
            ):
                pass

    first = threading.Thread(target=run, args=("Greet Lovelace",))
    first.start()
    time.sleep(0.1)
    run("Hi again")
    first.join()

    assert "Hello, Ada!" in [m.text for m in model.requests[2]]


async def _forget(thread: str) -> None:
    """Deletes thread from the developer's Postgres"""
    async with postgres_checkpointer(SecretStr(str(POSTGRES_URL))) as saver:
        await saver.adelete_thread(thread)


@pytest.mark.skipif(POSTGRES_URL is None, reason="needs POSTGRES_URL in .env")
def test_a_users_thread_survives_a_restart_with_postgres(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("POSTGRES_URL", str(POSTGRES_URL))
    thread = f"test-{uuid.uuid4()}"
    model = ScriptedChatModel(
        replies=[AIMessage("Hello, Ada!"), AIMessage("Your name is Ada.")]
    )

    for text in ("Hi, I'm Ada", "What's my name?"):
        with (
            TestClient(_app(model)) as client,
            client.websocket_connect(PATH) as socket,
        ):
            _run(socket, text, thread=thread)

    asyncio.run(_forget(f"greeter:{thread}"))

    assert _human(model, 1) == ["Hi, I'm Ada", "What's my name?"]
