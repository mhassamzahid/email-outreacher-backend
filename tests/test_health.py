from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_root_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_health() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "Outreacher API"


def test_database_routes_say_503_when_not_configured(monkeypatch) -> None:
    from config import get_settings
    from database import get_engine

    monkeypatch.setattr(get_settings(), "database_url", None)
    get_engine.cache_clear()
    response = client.get("/api/v1/icps")
    assert response.status_code == 503
    get_engine.cache_clear()


def test_cors_allows_frontend_origin() -> None:
    response = client.options(
        "/api/v1/health",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
