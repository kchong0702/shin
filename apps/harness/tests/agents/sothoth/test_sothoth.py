import re

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.agents import build_agent, load_agents
from src.agents.sothoth.tools import read_page
from src.app.app import create_app
from src.app.container import Container
from src.core.config import get_settings
from tests.conftest import ScriptedChatModel


def test_every_tool_the_system_prompt_names_is_one_sothoth_has():
    container = Container()
    agent = container.sothoth.agent.kwargs["agent"]()
    tools = {tool.name for tool in container.sothoth.agent.kwargs["tools"]}

    named = set(re.findall(r"`(\w+)`", agent.system_prompt.read_text()))

    assert named == tools == {"read_page"}


def test_the_app_serves_sothoth_over_a2a(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    card = TestClient(create_app()).get("/shin/a2a/sothoth/.well-known/agent-card.json")

    assert card.json()["name"] == "sothoth"


async def test_sothoth_reads_a_page_and_answers_from_it(site: str):
    agent = load_agents(get_settings().agents)["sothoth"]
    model = ScriptedChatModel(
        replies=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_page",
                        "args": {"url": f"{site}/index.html"},
                        "id": "call-1",
                    }
                ],
            ),
            AIMessage(f"The notes are titled Field notes ({site}/index.html)."),
        ]
    )

    result = await build_agent(agent, tools=(read_page,), model=model).ainvoke(
        {"messages": [HumanMessage("What are the field notes called?")]}
    )

    [page] = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert "Title: Field notes" in page.text
    assert "You are Sothoth" in model.requests[0][0].text
    assert "read_page" in model.offered_tools[0]
    assert result["messages"][-1].text.endswith(f"({site}/index.html).")
