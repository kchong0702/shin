from dependency_injector import containers, providers

from src.agents import build_agent
from src.agents.sothoth.tools import read_page
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
        checkpointer=checkpointer,
    )
    """The sothoth agent, built once"""

    executable = providers.Singleton(
        Executable,
        name="sothoth",
        description="Research questions answered by reading web pages, with every claim cited to its URL",
        graph=agent,
    )
    """sothoth served over A2A"""
