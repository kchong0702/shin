import re

from fastapi.testclient import TestClient

from src.agents import load_agents
from src.agents.sothoth.tools import BROWSER_TOOLS
from src.app.app import create_app
from src.core.config import get_settings


def test_every_tool_the_system_prompt_names_is_one_sothoth_has():
    agent = load_agents(get_settings().agents)["sothoth"]

    assert set(re.findall(r"`(\w+)`", agent.system_prompt.read_text())) <= BROWSER_TOOLS


def test_the_app_serves_sothoth_over_websocket():
    with TestClient(create_app()).websocket_connect("/zumi/ws/sothoth") as socket:
        socket.send_text("{}")

        assert socket.receive_json()["message"].startswith("Not a RunAgentInput")
