from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from functools import partial

from dependency_injector import containers, providers
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from src.agents import Agent, build_agent
from src.agents.sothoth.tools import exa_search_tool, open_browser, web_search_tool
from src.app.agui import ServedAgent
from src.core.config import BrowserCommand, BrowserServer, Settings


@asynccontextmanager
async def open_sothoth(
    agent: Agent,
    checkpointer: Callable[[], BaseCheckpointSaver],
    browser: BrowserCommand | BrowserServer,
    skill_tools: Sequence[BaseTool],
) -> AsyncIterator[CompiledStateGraph]:
    """Builds sothoth for one run with checkpointer's current saver, and a browser
    that closes when the run ends"""
    async with open_browser(browser) as browser_tools:
        yield build_agent(
            agent,
            tools=browser_tools,
            skill_tools=skill_tools,
            checkpointer=checkpointer(),
        )


class SothothContainer(containers.DeclarativeContainer):
    settings = providers.Dependency(instance_of=Settings)
    """The app's settings"""

    registry = providers.Dependency(instance_of=dict)
    """Every registered agent, by name"""

    checkpointer = providers.Dependency()
    """Where the agent keeps its conversations"""

    served = providers.Singleton(
        ServedAgent,
        name="sothoth",
        open_graph=providers.Factory(
            partial,
            open_sothoth,
            agent=registry.provided["sothoth"],
            checkpointer=checkpointer.provider,
            browser=settings.provided.browser,
            skill_tools=providers.List(
                providers.Callable(
                    web_search_tool,
                    settings.provided.search,
                    settings.provided.tavily_api_key,
                ),
                providers.Callable(
                    exa_search_tool,
                    settings.provided.search,
                    settings.provided.exa_api_key,
                ),
            ),
        ),
    )
    """sothoth served over WebSocket, built for each run"""
