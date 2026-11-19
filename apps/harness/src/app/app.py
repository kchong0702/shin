from fastapi import FastAPI

from src.app.agui import add_agent_routes
from src.app.container import Container
from src.core.config import get_settings


def health_check() -> dict[str, str]:
    """Reports that the server is up, with its version"""
    return {"status": "ok", "version": get_settings().app.version}


def create_app(container: Container | None = None) -> FastAPI:
    """Creates the FastAPI app serving the container's agents"""
    container = container or Container()
    settings = get_settings()
    app = FastAPI(
        title=settings.app.name.upper(),
        version=settings.app.version,
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
