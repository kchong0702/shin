from dependency_injector import containers, providers
from langgraph.checkpoint.memory import InMemorySaver

from src.agents import load_agents
from src.app.container.sothoth import SothothContainer
from src.core.config import get_settings


class Container(containers.DeclarativeContainer):
    settings = providers.Singleton(get_settings)
    """The app's settings"""

    registry = providers.Singleton(load_agents, settings.provided.agents)
    """Every registered agent, by name"""

    checkpointer = providers.Singleton(InMemorySaver)
    """Where agents keep each user's lifelong conversation"""

    sothoth = providers.Container(
        SothothContainer,
        settings=settings,
        registry=registry,
        checkpointer=checkpointer,
    )
    """sothoth, the research agent"""

    agents = providers.List(sothoth.served)
    """Agents served over WebSocket"""
