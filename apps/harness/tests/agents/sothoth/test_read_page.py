import socket
from collections.abc import Iterator

import pytest

from src.agents.sothoth.tools import read_page
from src.core.config import get_settings

LONG_TEXT = "word " * 100


@pytest.fixture
def limits(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("AMAYA_HARNESS_BROWSER__MAX_PAGE_CHARS", "120")
    monkeypatch.setenv("AMAYA_HARNESS_BROWSER__MAX_LINKS", "2")
    get_settings.cache_clear()
    yield
    monkeypatch.undo()
    get_settings.cache_clear()


async def test_a_page_comes_back_as_untrusted_data_with_its_title_and_address(
    site: str,
):
    result = await read_page.ainvoke({"url": f"{site}/index.html"})

    assert result.startswith("<page untrusted>\n")
    assert result.endswith("\n</page>")
    assert "Title: Field notes" in result
    assert f"URL: {site}/index.html" in result
    assert "Heading" in result


async def test_a_redirect_reports_the_address_it_ended_on(site: str):
    result = await read_page.ainvoke({"url": f"{site}/moved.html"})

    assert f"URL: {site}/index.html" in result


@pytest.mark.usefixtures("limits")
async def test_text_past_the_limit_is_cut_and_says_how_much(site: str):
    result = await read_page.ainvoke({"url": f"{site}/index.html"})

    shown = result.split("Links:")[0]
    assert "[cut: " in shown
    assert LONG_TEXT not in shown
    cut = int(shown.split("[cut: ")[1].split(" ")[0])
    assert cut > 0


async def test_links_come_back_absolute_with_their_text_each_address_once(site: str):
    result = await read_page.ainvoke({"url": f"{site}/index.html"})

    links = result.split("Links:\n")[1].removesuffix("\n</page>").splitlines()
    assert links == [
        f"- The paper: {site}/paper.html",
        "- Raw data: https://example.org/data",
        "- Appendix: https://example.org/appendix",
    ]


@pytest.mark.usefixtures("limits")
async def test_links_past_the_limit_are_left_out(site: str):
    result = await read_page.ainvoke({"url": f"{site}/index.html"})

    links = result.split("Links:\n")[1].removesuffix("\n</page>").splitlines()
    assert links == [
        f"- The paper: {site}/paper.html",
        "- Raw data: https://example.org/data",
    ]


async def test_a_page_that_cannot_be_reached_is_an_error_the_model_sees():
    with socket.socket() as closed:
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]

    result = await read_page.ainvoke({"url": f"http://127.0.0.1:{port}/"})

    assert result.startswith(f"Could not read http://127.0.0.1:{port}/")


async def test_a_page_that_is_not_http_is_refused_without_opening_it():
    result = await read_page.ainvoke({"url": "file:///etc/hosts"})

    assert result == "Could not read file:///etc/hosts: only http and https pages open"
