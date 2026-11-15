from a2a.server.tasks import InMemoryTaskStore
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

    task_store = providers.Singleton(InMemoryTaskStore)
    """Where the A2A server keeps every task; swap in a database store here"""

    checkpointer = providers.Singleton(InMemorySaver)
    """Where agents keep their conversations; pass it to each agent's build, and
    swap in a database saver here"""

    sothoth = providers.Container(
        SothothContainer,
        registry=registry,
        checkpointer=checkpointer,
        browser=settings.provided.browser,
    )
    """sothoth, the research agent"""

    executables = providers.List(sothoth.executable)
    """Agents served over A2A; add an agent's container above and its Executable
    provider here"""
