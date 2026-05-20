from fastapi.testclient import TestClient


def test_asgi_app_imports_without_external_credentials():
    from app.main import app

    assert app.title == "Expense Agent"


def test_health_endpoint_returns_service_identity():
    from app.main import app

    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "expense-agent",
    }
