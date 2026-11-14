from collections.abc import Sequence
from typing import Any, override

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field


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
