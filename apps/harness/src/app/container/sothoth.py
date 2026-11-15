from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial

from dependency_injector import containers, providers
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from src.agents import Agent, build_agent
from src.agents.sothoth.tools import exa_search, open_browser, web_search
from src.app.a2a import Executable
from src.core.config import BrowserCommand, BrowserServer


@asynccontextmanager
async def open_sothoth(
    agent: Agent,
    checkpointer: BaseCheckpointSaver,
    browser: BrowserCommand | BrowserServer,
) -> AsyncIterator[CompiledStateGraph]:
    """Builds sothoth for one run, with a browser that closes when the run ends"""
    async with open_browser(browser) as browser_tools:
        yield build_agent(
            agent,
            tools=browser_tools,
            skill_tools=(web_search, exa_search),
            checkpointer=checkpointer,
        )


class SothothContainer(containers.DeclarativeContainer):
    registry = providers.Dependency(instance_of=dict)
    """Every registered agent, by name"""

    checkpointer = providers.Dependency()
    """Where the agent keeps its conversations"""

    browser = providers.Dependency()
    """How sothoth reaches Playwright MCP"""

    executable = providers.Singleton(
        Executable,
        name="sothoth",
        description="Research questions answered by searching the web and browsing pages, with every claim cited to its URL",
        open_graph=providers.Factory(
            partial,
            open_sothoth,
            agent=registry.provided["sothoth"],
            checkpointer=checkpointer,
            browser=browser,
        ),
    )
    """sothoth served over A2A, built for each run"""
