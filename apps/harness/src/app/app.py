from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.app.agui import add_agent_routes
from src.app.container import Container
from src.clients.checkpointer import postgres_checkpointer
from src.core.config import get_settings


def health_check() -> dict[str, str]:
    """Reports that the server is up, with its version"""
    return {"status": "ok", "version": get_settings().app.version}


@asynccontextmanager
async def _lifespan(container: Container) -> AsyncIterator[None]:
    """Keeps conversations in the Postgres at POSTGRES_URL while the app runs, when
    it is set"""
    url = container.settings().postgres_url
    if url is None:
        yield
        return
    async with postgres_checkpointer(url) as saver:
        with container.checkpointer.override(saver):
            yield


def create_app(container: Container | None = None) -> FastAPI:
    """Creates the FastAPI app serving the container's agents"""
    container = container or Container()
    settings = get_settings()
    app = FastAPI(
        title=settings.app.name.upper(),
        version=settings.app.version,
        lifespan=lambda _: _lifespan(container),
        openapi_url=f"{settings.app.route_prefix}/openapi.json",
        docs_url=f"{settings.app.route_prefix}/docs",
    )
    app.add_api_route(
        path=f"{settings.app.route_prefix}/health",
        endpoint=health_check,
        methods=["GET"],
    )
    add_agent_routes(app, container.agents(), settings.app)
    return app
