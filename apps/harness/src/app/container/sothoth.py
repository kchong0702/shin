from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from functools import partial

from dependency_injector import containers, providers
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from src.agents import Agent, build_agent
from src.agents.sothoth.tools import open_browser
from src.app.agui import ServedAgent
from src.core.config import BrowserCommand, BrowserServer, Settings


@asynccontextmanager
async def open_sothoth(
    agent: Agent,
    checkpointer: Callable[[], BaseCheckpointSaver],
    browser: BrowserCommand | BrowserServer,
) -> AsyncIterator[CompiledStateGraph]:
    """Builds sothoth for one run with checkpointer's current saver, and a browser
    that closes when the run ends"""
    async with open_browser(browser) as browser_tools:
        yield build_agent(agent, tools=browser_tools, checkpointer=checkpointer())


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
        ),
    )
    """sothoth served over WebSocket, built for each run"""
