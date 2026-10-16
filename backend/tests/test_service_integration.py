"""PostgreSQL and Redis deployment-path smoke tests (CI services required)."""
import os

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_SERVICE_INTEGRATION") != "1",
    reason="Set RUN_SERVICE_INTEGRATION=1 with PostgreSQL and Redis services available.",
)


def test_postgres_redis_readiness_registration_and_shared_throttling():
    from fastapi.testclient import TestClient

    from backend.app.main import app

    with TestClient(app) as client:
        ready = client.get("/api/health/ready")
        assert ready.status_code == 200, ready.text
        assert ready.json()["database"] == "connected"
        assert ready.json()["rate_limit_store"] == "connected"

        registered = client.post("/api/auth/register", json={
            "email": "integration-user@example.com",
            "password": "IntegrationPass123!",
            "full_name": "Integration User",
            "role": "student",
            "department": "Computer Science",
            "skills": ["Python", "PostgreSQL"],
        })
        assert registered.status_code == 201, registered.text
        token = registered.json()["access_token"]
        profile = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert profile.status_code == 200
        assert profile.json()["profile"]["full_name"] == "Integration User"

        failures = [client.post("/api/auth/login", json={
            "email": "missing-account@example.com", "password": "IncorrectPass123!",
        }) for _ in range(6)]
        assert all(response.status_code == 401 for response in failures[:5])
        assert failures[5].status_code == 429
        assert failures[5].headers.get("retry-after") == "60"
