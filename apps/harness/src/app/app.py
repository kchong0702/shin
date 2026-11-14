from fastapi import FastAPI

from src.core.config import get_settings


def health_check() -> dict[str, str]:
    """Reports that the server is up, with its version"""
    return {"status": "ok", "version": get_settings().app.version}


def create_app() -> FastAPI:
    """Creates the FastAPI app"""
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
    return app
