"""The response cache (response_cache.py), on a small app of its own so every call is counted."""

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import response_cache
from config import get_settings

SECRET = "s3cret"


@pytest.fixture
def cached(monkeypatch):
    monkeypatch.setattr(get_settings(), "api_secret", SECRET)
    monkeypatch.setattr(get_settings(), "response_cache_seconds", 60)
    response_cache.clear()
    calls = {"reads": 0}
    app = FastAPI()
    app.add_middleware(response_cache.ResponseCache)

    @app.get("/api/v1/things")
    def things():
        calls["reads"] += 1
        return {"reads": calls["reads"]}

    @app.post("/api/v1/things")
    def change():
        return {"ok": True}

    @app.get("/api/v1/broken")
    def broken():
        calls["reads"] += 1
        return JSONResponse({"detail": "no"}, status_code=500)

    @app.get("/api/v1/jobs/1")
    def job():
        calls["reads"] += 1
        return {"status": "running"}

    yield TestClient(app), calls
    response_cache.clear()


def h(token="a", **extra):
    return {"Authorization": f"Bearer {token}", "X-Coldbrew-Secret": SECRET, "X-Workspace-Id": "w1", **extra}


def test_a_repeat_read_comes_from_memory(cached) -> None:
    client, calls = cached
    assert client.get("/api/v1/things", headers=h()).json() == {"reads": 1}
    assert client.get("/api/v1/things", headers=h()).json() == {"reads": 1}
    assert calls["reads"] == 1


def test_answers_belong_to_one_session_workspace_and_url(cached) -> None:
    client, calls = cached
    client.get("/api/v1/things", headers=h())
    client.get("/api/v1/things", headers=h(token="b"))
    client.get("/api/v1/things", headers=h(**{"X-Workspace-Id": "w2"}))
    client.get("/api/v1/things?x=1", headers=h())
    assert calls["reads"] == 4


def test_any_change_clears_it(cached) -> None:
    client, calls = cached
    client.get("/api/v1/things", headers=h())
    client.post("/api/v1/things", headers=h(token="someone-else"))
    assert client.get("/api/v1/things", headers=h()).json() == {"reads": 2}


def test_refresh_skips_it_and_keeps_the_new_answer(cached) -> None:
    client, calls = cached
    client.get("/api/v1/things", headers=h())
    assert client.get("/api/v1/things", headers=h(**{"X-Coldbrew-Fresh": "1"})).json() == {"reads": 2}
    assert client.get("/api/v1/things", headers=h()).json() == {"reads": 2}


def test_it_expires(cached, monkeypatch) -> None:
    client, calls = cached
    client.get("/api/v1/things", headers=h())
    clock = response_cache.time.monotonic()
    monkeypatch.setattr(response_cache.time, "monotonic", lambda: clock + 61)
    assert client.get("/api/v1/things", headers=h()).json() == {"reads": 2}


def test_never_for_a_stranger_errors_or_live_endpoints(cached) -> None:
    client, calls = cached
    client.get("/api/v1/things", headers=h())
    stranger = {**h(), "X-Coldbrew-Secret": "guess"}
    client.get("/api/v1/things", headers=stranger)
    client.get("/api/v1/things", headers={**h(), "Origin": "https://evil.example"})
    assert calls["reads"] == 3  # both went the normal way (the real app refuses them there)
    for _ in range(2):
        client.get("/api/v1/broken", headers=h())
        client.get("/api/v1/jobs/1", headers=h())
    assert calls["reads"] == 7


def test_off_when_set_to_zero(cached, monkeypatch) -> None:
    client, calls = cached
    monkeypatch.setattr(get_settings(), "response_cache_seconds", 0)
    client.get("/api/v1/things", headers=h())
    client.get("/api/v1/things", headers=h())
    assert calls["reads"] == 2


def test_a_change_clears_it_before_its_answer_arrives(cached) -> None:
    """The browser's next read can follow the answer at once: by then the old answers must be gone."""
    client, calls = cached
    client.get("/api/v1/things", headers=h())
    seen = []
    real_clear = response_cache.clear

    def watched() -> None:
        seen.append(len(response_cache._entries))
        real_clear()

    import unittest.mock

    with unittest.mock.patch.object(response_cache, "clear", watched):
        r = client.post("/api/v1/things", headers=h())
    assert r.status_code == 200 and seen == [1]  # cleared exactly once, while answering
    assert client.get("/api/v1/things", headers=h()).json() == {"reads": 2}
