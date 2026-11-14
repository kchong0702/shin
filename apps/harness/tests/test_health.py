from dependency_injector import providers
from fastapi.testclient import TestClient

from src.app.app import create_app
from src.app.container import Container


def test_health():
    container = Container()
    container.executables.override(providers.List())

    response = TestClient(create_app(container)).get("/shin/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
