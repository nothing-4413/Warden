"""M5：可选前端（简单控制台页面 + 运行记录端点）。"""
from fastapi.testclient import TestClient

from app.main import app


def test_dashboard_html():
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Warden" in resp.text


def test_runs_endpoint_returns_list():
    client = TestClient(app)
    resp = client.get("/api/v1/runs")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
