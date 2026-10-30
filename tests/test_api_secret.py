"""Only Coldbrew's frontend server reaches the API: the shared secret, then an allowed origin, before sign-in."""

import pytest
from fastapi.testclient import TestClient

from config import origin_of
from dependencies import get_user_repo, require_api_secret
from main import app

SECRET = "s3cret-for-tests"
OK = {"X-Coldbrew-Secret": SECRET}


@pytest.fixture
def client(monkeypatch):
    from config import get_settings

    monkeypatch.setattr(get_settings(), "api_secret", SECRET)
    monkeypatch.setattr(get_settings(), "authorized_urls", "https://app.example.com, http://localhost:3000/")
    app.dependency_overrides.pop(require_api_secret, None)  # the real check, not the test bypass
    # No real database: past the secret, sign-in stops at the missing token anyway.
    app.dependency_overrides[get_user_repo] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.pop(get_user_repo, None)


def test_no_secret_is_refused_before_sign_in(client) -> None:
    # 401 "Not authorized." rather than "Sign in to continue.": the token is never looked at.
    r = client.get("/api/v1/me", headers={"Authorization": "Bearer whatever"})
    assert (r.status_code, r.json()["detail"]) == (401, "Not authorized.")


def test_a_wrong_secret_is_refused(client) -> None:
    assert client.get("/api/v1/me", headers={"X-Coldbrew-Secret": SECRET + "x"}).status_code == 401


def test_the_right_secret_goes_on_to_sign_in(client) -> None:
    r = client.get("/api/v1/me", headers=OK)
    assert (r.status_code, r.json()["detail"]) == (401, "Sign in to continue.")


@pytest.mark.parametrize(
    ("header", "value", "allowed"),
    [
        ("Origin", "https://app.example.com", True),
        ("Origin", "http://localhost:3000", True),
        ("Origin", "https://evil.example", False),
        ("Origin", "null", False),
        ("Referer", "https://app.example.com/icps?x=1", True),
        ("Referer", "https://app.example.com.evil.example/", False),
    ],
)
def test_only_authorized_urls_may_call(client, header, value, allowed) -> None:
    r = client.get("/api/v1/me", headers={**OK, header: value})
    assert r.status_code == (401 if allowed else 403)  # 401: through to sign-in


def test_without_a_configured_secret_the_api_refuses_everything(client, monkeypatch) -> None:
    from config import get_settings

    monkeypatch.setattr(get_settings(), "api_secret", None)
    assert client.get("/api/v1/me", headers=OK).status_code == 503


def test_health_needs_no_secret(client) -> None:
    assert client.get("/api/v1/health").status_code == 200 and client.get("/health").status_code == 200


def test_cors_only_answers_authorized_urls(client) -> None:
    from main import create_app

    fresh = TestClient(create_app())
    preflight = {"Access-Control-Request-Method": "GET"}
    allowed = fresh.options("/api/v1/me", headers={**preflight, "Origin": "https://app.example.com"})
    refused = fresh.options("/api/v1/me", headers={**preflight, "Origin": "https://evil.example"})
    assert allowed.headers.get("access-control-allow-origin") == "https://app.example.com"
    assert "access-control-allow-origin" not in refused.headers


def test_origin_of() -> None:
    assert origin_of("HTTPS://App.Example.com:8443/path?q") == "https://app.example.com:8443"
    assert origin_of("null") is None and origin_of("/icps") is None and origin_of("ftp://x.com") is None
