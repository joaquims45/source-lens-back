from fastapi.testclient import TestClient

from sourcelens.main import create_app


def test_health_and_problem_response():
    with TestClient(create_app()) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        result = client.get("/missing")
        assert result.status_code == 404
        assert result.headers["content-type"] == "application/problem+json"
        assert result.json()["request_id"] == result.headers["x-request-id"]
