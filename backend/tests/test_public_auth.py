import base64
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import main
from app.config import settings


def auth_header(username="solo", password="test-password"):
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_anonymous_http_and_api_requests_are_rejected(monkeypatch):
    from app.auth import is_authorized
    monkeypatch.setattr(settings, "app_username", "solo")
    monkeypatch.setattr(settings, "app_password", "test-password")
    client = TestClient(main.app)
    assert not is_authorized(None)
    response = client.get("/api/search?q=NVDA")
    assert response.status_code == 401


def test_valid_basic_auth_can_load_the_spa(monkeypatch):
    monkeypatch.setattr(settings, "app_username", "solo")
    monkeypatch.setattr(settings, "app_password", "test-password")
    client = TestClient(main.app)
    response = client.get("/", headers=auth_header())
    assert response.status_code == 200
    assert "Stock Signal" in response.text
    assert client.get("/watchlist/NVDA", headers=auth_header()).text == response.text
    assert client.get("/assets/not-a-real-file.js", headers=auth_header()).status_code == 404


def test_production_without_credentials_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "app_environment", "production")
    monkeypatch.setattr(settings, "app_username", "")
    monkeypatch.setattr(settings, "app_password", "")
    response = TestClient(main.app).get("/")
    assert response.status_code == 503


def test_websocket_rejects_missing_auth_and_accepts_valid_basic_auth(monkeypatch):
    from app.auth import is_authorized
    monkeypatch.setattr(settings, "app_username", "solo")
    monkeypatch.setattr(settings, "app_password", "test-password")
    assert not is_authorized(None)
    assert is_authorized(auth_header()["Authorization"])
    client = TestClient(main.app)
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/api/ws/trades"):
            pass
    assert closed.value.code == 4401
    with client.websocket_connect("/api/ws/trades", headers=auth_header()) as websocket:
        websocket.close()
