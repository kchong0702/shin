import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator, Sequence
from contextlib import aclosing
from functools import partial
from uuid import uuid4

import structlog
from ag_ui.core import (
    BaseEvent,
    EventType,
    RunAgentInput,
    RunErrorEvent,
    RunStartedEvent,
    UserMessage,
)
from ag_ui_langgraph import LangGraphAgent
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status
from pydantic import ValidationError

from src.app.agui.served_agent import ServedAgent
from src.core.config import AppSettings


def _first_error(error: BaseException) -> BaseException:
    """The first error inside the exception groups wrapping error"""
    if isinstance(error, BaseExceptionGroup):
        return _first_error(error.exceptions[0])
    return error


def _refusal(reason: str) -> str:
    """A RUN_ERROR event saying why a message was not run, as JSON"""
    return RunErrorEvent(type=EventType.RUN_ERROR, message=reason).model_dump_json(
        by_alias=True, exclude_none=True
    )


def _turn(served: ServedAgent, run: RunAgentInput) -> RunAgentInput | None:
    """The client's run as one new user turn on the agent's own copy of the thread,
    or None when it has no thread or no user message"""
    text = next((m.content for m in reversed(run.messages) if m.role == "user"), None)
    if not run.thread_id or text is None:
        return None
    return RunAgentInput(
        thread_id=f"{served.name}:{run.thread_id}",
        run_id=run.run_id,
        messages=[UserMessage(id=str(uuid4()), role="user", content=text)],
    )


async def _events(served: ServedAgent, run: RunAgentInput) -> AsyncIterator[BaseEvent]:
    """One run's AG-UI events, without the snapshots of the whole thread, always
    between one RUN_STARTED and one RUN_FINISHED or RUN_ERROR"""
    started = finished = False
    try:
        async with (
            served.open_graph() as graph,
            aclosing(
                LangGraphAgent(
                    name=served.name,
                    graph=graph,
                    config=graph.config,
                    emit_raw_events=False,
                ).run(run)
            ) as events,
        ):
            async for event in events:
                if event.type in (
                    EventType.MESSAGES_SNAPSHOT,
                    EventType.STATE_SNAPSHOT,
                ):
                    continue
                started = started or event.type == EventType.RUN_STARTED
                finished = finished or event.type in (
                    EventType.RUN_FINISHED,
                    EventType.RUN_ERROR,
                )
                yield event
    except* Exception as errors:
        error = _first_error(errors)
        if finished:
            structlog.get_logger().error(
                "closing the run failed", agent=served.name, error=str(error)
            )
        else:
            if not started:
                yield RunStartedEvent(
                    type=EventType.RUN_STARTED,
                    thread_id=run.thread_id,
                    run_id=run.run_id,
                )
            yield RunErrorEvent(
                type=EventType.RUN_ERROR, message=str(error) or type(error).__name__
            )


async def _serve(
    served: ServedAgent, threads: defaultdict[str, asyncio.Lock], websocket: WebSocket
) -> None:
    """Runs the agent on each RunAgentInput the client sends, one run at a time per
    thread, and sends back each run's AG-UI events until the client disconnects"""
    if "origin" in websocket.headers:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await websocket.accept()
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            try:
                run = RunAgentInput.model_validate_json(
                    message.get("text") or message.get("bytes") or ""
                )
            except ValidationError as error:
                await websocket.send_text(_refusal(f"Not a RunAgentInput: {error}"))
                continue
            turn = _turn(served, run)
            if turn is None:
                await websocket.send_text(
                    _refusal("A run needs a threadId and a user message")
                )
                continue
            async with (
                threads[turn.thread_id],
                aclosing(_events(served, turn)) as events,
            ):
                async for event in events:
                    await websocket.send_text(
                        event.model_dump_json(by_alias=True, exclude_none=True)
                    )
    except WebSocketDisconnect:
        return


def add_agent_routes(
    app: FastAPI, agents: Sequence[ServedAgent], settings: AppSettings
) -> None:
    """Serves each agent over a WebSocket at <route_prefix>/ws/<name>, speaking AG-UI,
    and refuses connections from browsers"""
    threads: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
    for served in agents:
        app.add_api_websocket_route(
            f"{settings.route_prefix}/ws/{served.name}",
            partial(_serve, served, threads),
        )
