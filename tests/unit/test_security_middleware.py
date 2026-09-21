from fastapi.testclient import TestClient

from sourcelens.config import Settings
from sourcelens.main import create_app

# These tests only exercise the auth/CORS middleware itself, never a route
# handler that needs a database (a 401 from the middleware short-circuits
# before get_db runs; CORS preflight is handled by CORSMiddleware directly),
# so they stay pure unit tests unlike anything that reaches a real DomainError
# from a route.


def test_health_check_is_never_gated_by_api_key():
    settings = Settings(api_key="secret")
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/live").status_code == 200


def test_api_routes_require_the_configured_api_key():
    settings = Settings(api_key="secret")
    with TestClient(create_app(settings)) as client:
        unauthenticated = client.get("/api/v1/analyses/00000000-0000-0000-0000-000000000000")
        assert unauthenticated.status_code == 401
        assert unauthenticated.headers["content-type"] == "application/problem+json"

        wrong_key = client.get(
            "/api/v1/analyses/00000000-0000-0000-0000-000000000000",
            headers={"X-API-Key": "not-it"},
        )
        assert wrong_key.status_code == 401


def test_cors_reflects_only_configured_origins():
    settings = Settings(cors_allow_origins="https://sourcelens.example")
    with TestClient(create_app(settings)) as client:
        allowed = client.options(
            "/api/v1/analyses/00000000-0000-0000-0000-000000000000",
            headers={
                "Origin": "https://sourcelens.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert allowed.headers["access-control-allow-origin"] == "https://sourcelens.example"

        blocked = client.options(
            "/api/v1/analyses/00000000-0000-0000-0000-000000000000",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
        )
        assert "access-control-allow-origin" not in blocked.headers
