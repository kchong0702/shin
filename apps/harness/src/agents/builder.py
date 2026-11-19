from collections.abc import Mapping, Sequence
from functools import partial

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.skills import SkillsMiddleware
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime

from src.agents.registry import Agent
from src.clients.llm_providers import llm_provider
from src.core.config import get_settings


def _described(tool: BaseTool, descriptions: Mapping[str, str]) -> BaseTool:
    """tool with the agent profile's description, when the profile has one for it"""
    if tool.name in descriptions:
        return tool.model_copy(update={"description": descriptions[tool.name]})
    return tool


def _skill_tool(tools: Mapping[str, BaseTool], name: str, _: Runtime) -> list[BaseTool]:
    """The skill tool called name, when the agent has one"""
    return [tools[name]] if name in tools else []


def build_agent(
    agent: Agent,
    *,
    tools: Sequence[BaseTool] = (),
    skill_tools: Sequence[BaseTool] = (),
    middleware: Sequence[AgentMiddleware] = (),
    model: BaseChatModel | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """Builds a registered agent as a deep agent that reads its skills and keeps the
    history it summarises away in its thread, offered skill_tools by skill"""
    backend = CompositeBackend(
        default=FilesystemBackend(root_dir=agent.skills, virtual_mode=True),
        routes={"/conversation_history/": StateBackend()},
    )
    descriptions = agent.profile.tool_description_overrides

    built = create_deep_agent(
        model=model or llm_provider(agent.family, agent.model),
        tools=[_described(tool, descriptions) for tool in tools],
        system_prompt=agent.system_prompt.read_text(),
        middleware=[
            FilesystemMiddleware(
                backend=backend, tools=["ls", "read_file", "glob", "grep"]
            ),
            *middleware,
            *(
                [
                    SkillsMiddleware(
                        backend=backend,
                        sources=["/"],
                        tools=partial(
                            _skill_tool,
                            {
                                tool.name: _described(tool, descriptions)
                                for tool in skill_tools
                            },
                        ),
                    )
                ]
                if agent.skills.is_dir()
                else []
            ),
        ],
        permissions=[
            FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")
        ],
        backend=backend,
        checkpointer=checkpointer,
        name=agent.name,
    )
    return built.with_config(recursion_limit=get_settings().recursion_limit)
