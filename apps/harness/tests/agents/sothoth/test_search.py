import importlib
import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, override
from urllib.parse import parse_qs, urlparse

import pytest

from src.agents.sothoth.tools import exa_search, web_search
from src.core.config import Settings, get_settings

search_module = importlib.import_module("src.agents.sothoth.tools.search")

NOTES = {
    "title": "Field notes",
    "url": "https://example.org/notes",
    "content": "A snippet of the notes",
    "raw_content": "The whole text of the notes",
}
PAPER = {"title": "A paper", "url": "https://example.org/paper", "text": "Abstract"}
SNIPPET = {"title": "Notes", "url": "https://example.org/n", "content": "Short"}
FALLBACK = "1. Notes\nURL: https://example.org/n\nSnippet:\nShort"


@dataclass
class Services:
    url: str
    answers: dict[str, tuple[int, dict[str, Any]]] = field(
        default_factory=lambda: {
            "/tavily/search": (200, {"results": [NOTES]}),
            "/exa/search": (200, {"results": [PAPER]}),
            "/searxng/search": (200, {"results": [SNIPPET]}),
        }
    )
    requests: dict[str, dict[str, Any]] = field(default_factory=dict)


@pytest.fixture
def services(monkeypatch: pytest.MonkeyPatch) -> Iterator[Services]:
    class Handler(BaseHTTPRequestHandler):
        def _answer(self, body: dict[str, Any]) -> None:
            path = urlparse(self.path).path
            found.requests[path] = body
            status, answer = found.answers[path]
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(answer).encode())

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self._answer({**body, "headers": dict(self.headers)})

        def do_GET(self) -> None:
            self._answer(parse_qs(urlparse(self.path).query))

        @override
        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    found = Services(url=f"http://127.0.0.1:{server.server_port}")
    for name in ("tavily", "exa", "searxng"):
        monkeypatch.setenv(
            f"ZUMI_HARNESS_SEARCH__{name.upper()}_URL", f"{found.url}/{name}"
        )
    monkeypatch.setenv("ZUMI_HARNESS_SEARCH__MAX_CONTENT_CHARS", "20")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-key")
    monkeypatch.setenv("EXA_API_KEY", "exa-key")
    get_settings.cache_clear()
    yield found
    server.shutdown()
    monkeypatch.undo()
    get_settings.cache_clear()


async def test_web_search_asks_tavily_and_gives_page_text_as_untrusted_data(
    services: Services,
):
    result = await web_search.ainvoke({"query": "field notes"})

    assert result.startswith("<search untrusted>\nResults for: field notes\n\n")
    assert result.endswith("\n</search>")
    assert "1. Field notes\nURL: https://example.org/notes\nPage text:\n" in result
    request = services.requests["/tavily/search"]
    assert request["headers"]["Authorization"] == "Bearer tavily-key"
    assert request["query"] == "field notes"
    assert "/searxng/search" not in services.requests


async def test_page_text_past_the_limit_is_cut_and_says_how_much(services: Services):
    result = await web_search.ainvoke({"query": "field notes"})

    assert "Page text:\nThe whole text of th\n[cut: 7 more characters]" in result


async def test_tavily_without_page_text_gives_its_snippet(services: Services):
    services.answers["/tavily/search"] = (
        200,
        {"results": [{**NOTES, "raw_content": None}]},
    )

    result = await web_search.ainvoke({"query": "field notes"})

    assert "Snippet:\nA snippet of the not" in result


async def test_exa_search_asks_exa_with_the_category(services: Services):
    result = await exa_search.ainvoke(
        {"query": "papers on GUI agents", "category": "publication"}
    )

    assert "1. A paper\nURL: https://example.org/paper\nPage text:\nAbstract" in result
    request = services.requests["/exa/search"]
    assert request["headers"]["x-api-key"] == "exa-key"
    assert request["category"] == "publication"


async def test_exa_search_without_a_category_sends_none(services: Services):
    await exa_search.ainvoke({"query": "papers on GUI agents"})

    assert "category" not in services.requests["/exa/search"]


@pytest.mark.parametrize(
    ("tool", "path", "status"),
    [(web_search, "/tavily/search", 432), (exa_search, "/exa/search", 402)],
)
async def test_searxng_answers_when_the_paid_service_is_out_of_credit(
    services: Services, tool: Any, path: str, status: int
):
    services.answers[path] = (status, {"error": "out of credit"})

    result = await tool.ainvoke({"query": "field notes"})

    assert FALLBACK in result
    assert services.requests["/searxng/search"]["format"] == ["json"]


async def test_searxng_answers_without_paid_keys(
    services: Services, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("TAVILY_API_KEY")
    monkeypatch.delenv("EXA_API_KEY")
    monkeypatch.setattr(search_module, "get_settings", lambda: Settings(_env_file=None))

    results = [
        await web_search.ainvoke({"query": "field notes"}),
        await exa_search.ainvoke({"query": "field notes"}),
    ]

    assert all(FALLBACK in result for result in results)
    assert "/tavily/search" not in services.requests
    assert "/exa/search" not in services.requests


async def test_a_search_every_service_fails_is_an_error_the_model_sees(
    services: Services,
):
    services.answers["/tavily/search"] = (432, {})
    services.answers["/searxng/search"] = (503, {})

    result = await web_search.ainvoke({"query": "field notes"})

    assert result.startswith("Search failed. tavily: Client error '432")
    assert "; searxng: Server error '503" in result
