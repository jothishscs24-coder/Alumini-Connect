"""Safety and repeatability checks for the local synthetic showcase data."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def run_seed(database_url: str, app_env: str = "development") -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.update({"APP_ENV": app_env, "DATABASE_URL": database_url})
    env.pop("JWT_SECRET", None)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "backend.seed_demo"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )


def test_demo_seed_is_repeatable_and_creates_only_showcase_accounts(tmp_path):
    database = tmp_path / "showcase.db"
    database_url = f"sqlite:///{database}"

    first = run_seed(database_url)
    assert first.returncode == 0, first.stderr
    second = run_seed(database_url)
    assert second.returncode == 0, second.stderr

    with sqlite3.connect(database) as connection:
        users = connection.execute(
            "SELECT role, is_verified, COUNT(*) FROM users GROUP BY role, is_verified"
        ).fetchall()
        profiles = connection.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
        emails = {
            row[0]
            for row in connection.execute("SELECT email FROM users")
        }

    assert users == [("alumni", 1, 4), ("student", 0, 1)]
    assert profiles == 5
    assert emails == {
        "demo.student@example.com",
        "demo.alumni.maya@example.com",
        "demo.alumni.arjun@example.com",
        "demo.alumni.nisha@example.com",
        "demo.alumni.kabir@example.com",
    }


@pytest.mark.parametrize(
    ("app_env", "database_url", "message"),
    [
        ("production", "sqlite:///production.db", "development or test"),
        ("staging", "sqlite:///staging.db", "development or test"),
        ("development", "postgresql://localhost/alumni", "SQLite database"),
    ],
)
def test_demo_seed_refuses_shared_environments_and_non_sqlite_databases(
    app_env, database_url, message
):
    result = run_seed(database_url, app_env)
    assert result.returncode != 0
    assert message in result.stderr
