from contextlib import nullcontext
from functools import partial

from dependency_injector import containers, providers

from src.agents import build_agent
from src.agents.sothoth.tools import exa_search, read_page, web_search
from src.app.a2a import Executable


class SothothContainer(containers.DeclarativeContainer):
    registry = providers.Dependency(instance_of=dict)
    """Every registered agent, by name"""

    checkpointer = providers.Dependency()
    """Where the agent keeps its conversations"""

    agent = providers.Singleton(
        build_agent,
        agent=registry.provided["sothoth"],
        tools=(read_page,),
        skill_tools=(web_search, exa_search),
        checkpointer=checkpointer,
    )
    """The sothoth agent, built once"""

    executable = providers.Singleton(
        Executable,
        name="sothoth",
        description="Research questions answered by searching the web and reading pages, with every claim cited to its URL",
        open_graph=providers.Factory(partial, nullcontext, agent),
    )
    """sothoth served over A2A"""
