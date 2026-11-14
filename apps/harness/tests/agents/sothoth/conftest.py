import threading
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


@pytest.fixture
def site(tmp_path: Path) -> Iterator[str]:
    (tmp_path / "index.html").write_text(
        f"""<html><head><title>Field notes</title></head><body>
        <h1>Heading</h1><p>{"word " * 100}</p>
        <a href="/paper.html">The paper</a>
        <a href="/paper.html">The paper again</a>
        <a href="https://example.org/data">  Raw
        data </a>
        <a href="https://example.org/appendix">Appendix</a>
        <a href="mailto:me@example.org">Email</a>
        </body></html>"""
    )
    (tmp_path / "moved.html").write_text(
        "<html><head><title>Moved</title></head><body>"
        '<script>location.replace("/index.html")</script></body></html>'
    )
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=tmp_path)
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
