"""
tests/test_spa_routes.py
------------------------
The API serves the built SPA (ADR-0013). Offline — a fake build is written to a
temp dir, so this runs without Node.

The invariants: the SPA's own paths return index.html (uncached, since it names
the current hashed bundle); the permalink shape `/?run=<id>` keeps resolving;
and an unknown API path still 404s rather than answering with HTML.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import api.main as api_main  # noqa: E402

SPA = "<!doctype html><div id=root></div>"


@pytest.fixture
def client(tmp_path, monkeypatch):
    index = tmp_path / "index.html"
    index.write_text(SPA)
    monkeypatch.setattr(api_main, "_spa_index", str(index))
    return TestClient(api_main.app)


@pytest.mark.parametrize("path", ["/", "/?run=K_nruMgF8_gf", "/research", "/research?q=Analyse%20Apple",
                                  "/company/cik:320193", "/company/name%3Aacme-corp"])
def test_spa_paths_serve_the_app(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    assert resp.text == SPA
    assert resp.headers["cache-control"] == "no-cache"


def test_unknown_api_paths_still_404(client):
    assert client.get("/no-such-endpoint").status_code == 404
    assert client.get("/runs").status_code in (404, 405)


def test_api_routes_are_not_shadowed(client):
    assert client.get("/health").json()["status"] == "ok"


def test_without_a_build_the_root_says_how_to_build(tmp_path, monkeypatch):
    monkeypatch.setattr(api_main, "_spa_index", str(tmp_path / "missing.html"))
    body = TestClient(api_main.app).get("/").json()
    assert "npm run build" in body["build"]
