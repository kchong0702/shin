import uvicorn

from src.core.config import get_settings


def main() -> None:
    """Starts the uvicorn server"""
    settings = get_settings()
    uvicorn.run(
        "src.app.app:create_app",
        factory=True,
        host=settings.app.host,
        port=settings.app.port,
        log_level=settings.app.log_level,
    )


if __name__ == "__main__":
    main()
