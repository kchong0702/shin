import json
import os
from collections.abc import Iterator, Sequence
from typing import Any, override

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field

from src.core.config import Settings, get_settings

POSTGRES_URL = os.environ.get("POSTGRES_URL")
"""The developer's Postgres from .env, which only tests that ask for it use"""


@pytest.fixture(autouse=True)
def _in_memory(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class ScriptedChatModel(BaseChatModel):
    replies: list[AIMessage]
    requests: list[list[BaseMessage]] = Field(default_factory=list)
    offered_tools: list[set[str]] = Field(default_factory=list)
    tool_descriptions: dict[str, str] = Field(default_factory=dict)

    @property
    @override
    def _llm_type(self) -> str:
        return "scripted"

    @override
    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Runnable:
        functions = [convert_to_openai_tool(tool)["function"] for tool in tools]
        self.offered_tools.append({function["name"] for function in functions})
        self.tool_descriptions.update(
            {
                function["name"]: function.get("description", "")
                for function in functions
            }
        )
        return self

    @override
    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.requests.append(messages)
        reply = self.replies[min(len(self.requests), len(self.replies)) - 1]
        return ChatResult(generations=[ChatGeneration(message=reply)])

    @override
    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        reply = self._generate(messages, stop, run_manager, **kwargs).generations[0]
        message = reply.message
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content=message.content,
                tool_call_chunks=[
                    tool_call_chunk(
                        name=call["name"],
                        args=json.dumps(call["args"]),
                        id=call["id"],
                        index=index,
                    )
                    for index, call in enumerate(getattr(message, "tool_calls", []))
                ],
            )
        )
