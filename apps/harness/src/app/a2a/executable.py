from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Literal, override

from a2a.helpers import new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Message, TaskState
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import TaskPayload, TaskResultPayload
from pydantic import BaseModel, ConfigDict, Field


class Step(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, use_attribute_docstrings=True
    )

    id: str
    """The tool call the step is"""

    name: str
    """The tool the call runs"""

    status: Literal["running", "done", "failed"]
    """Where the call is"""


class Executable(BaseModel):
    model_config = ConfigDict(
        frozen=True, arbitrary_types_allowed=True, use_attribute_docstrings=True
    )

    name: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    """Path segment and agent card name, such as sothoth"""

    description: str
    """What the agent does, shown on its agent card"""

    open_graph: Callable[[], AbstractAsyncContextManager[CompiledStateGraph]]
    """Opens the agent's graph for one run, with anything the run holds open, such as
    a browser, closed when the run ends; the graph's checkpointer keeps a
    conversation's turns"""


def _steps(task: TaskPayload | TaskResultPayload) -> list[Step]:
    """The steps a tools task starts or ends, one per tool call"""
    if task["name"] != "tools":
        return []
    if "input" in task:
        return [
            Step(id=call["id"], name=call["name"], status="running")
            for call in task["input"]
        ]
    return [
        Step(
            id=message.tool_call_id,
            name=message.name or "",
            status="failed" if message.status == "error" else "done",
        )
        for message in task["result"].get("messages", [])
        if isinstance(message, ToolMessage)
    ]


def _reason(updater: TaskUpdater, text: str) -> Message:
    """An agent message carrying the reason a run stopped"""
    return updater.new_agent_message([new_text_part(text)])


class LanggraphExecutor(AgentExecutor):
    def __init__(self, executable: Executable) -> None:
        """Runs the executable's graph as one A2A task per message"""
        self.executable = executable

    @override
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Runs the graph on the user's text in the context's conversation, streaming
        its steps and text, and ends the task completed or failed"""
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        if context.current_task is None and context.message:
            await event_queue.enqueue_event(new_task_from_user_message(context.message))
        await updater.start_work()
        result = {}
        opened: set[str] = set()
        try:
            async with self.executable.open_graph() as graph:
                async for namespace, mode, data in graph.astream(
                    {"messages": [HumanMessage(context.get_user_input())]},
                    {"configurable": {"thread_id": context.context_id}},
                    stream_mode=["values", "messages", "tasks"],
                    subgraphs=True,
                ):
                    if mode == "values" and not namespace:
                        result = data
                    elif mode == "messages" and not namespace:
                        chunk, meta = data
                        if not (
                            isinstance(chunk, AIMessage)
                            and meta["langgraph_node"] == "model"
                        ):
                            continue
                        for block in chunk.content_blocks:
                            if block["type"] != "text" or not block["text"]:
                                continue
                            artifact_id = f"text-{meta['langgraph_checkpoint_ns']}"
                            await updater.add_artifact(
                                [new_text_part(block["text"])],
                                artifact_id=artifact_id,
                                name="text",
                                append=artifact_id in opened,
                            )
                            opened.add(artifact_id)
                    elif mode == "tasks" and not namespace:
                        for step in _steps(data):
                            await updater.update_status(
                                TaskState.TASK_STATE_WORKING,
                                updater.new_agent_message(
                                    [new_data_part({"step": step.model_dump()})]
                                ),
                            )
        except GraphRecursionError:
            await updater.failed(_reason(updater, "Ran out of steps before finishing"))
            return
        except Exception as error:
            await updater.failed(_reason(updater, f"The run failed: {error}"))
            return
        await updater.add_artifact(
            [new_text_part(result["messages"][-1].text)], name=self.executable.name
        )
        await updater.complete()

    @override
    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Marks the task cancelled once the SDK has stopped the run"""
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()
