"""Real-browser smoke and stored-XSS regression tests (run with RUN_BROWSER_E2E=1)."""
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_BROWSER_E2E") != "1",
    reason="Set RUN_BROWSER_E2E=1 and install Playwright Chromium to run browser tests.",
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def live_app(tmp_path_factory):
    from playwright.sync_api import sync_playwright

    # Fail with a clear setup message rather than silently claiming browser coverage.
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            browser.close()
    except Exception as exc:
        pytest.fail(f"Browser E2E was enabled but Chromium could not launch: {exc}")

    database = tmp_path_factory.mktemp("alumniconnect-browser") / "browser.db"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    env = dict(os.environ)
    env.update({
        "APP_ENV": "test",
        "DATABASE_URL": f"sqlite:///{database}",
        "JWT_SECRET": "browser-e2e-test-secret-not-for-deployment-2026",
        "ENABLE_LEGACY_DEMO_STATE": "false",
        "ADMIN_EMAIL": "browser-admin@example.com",
        "ADMIN_PASSWORD": "BrowserAdminPass123!",
        "ENABLE_API_DOCS": "false",
    })
    env.pop("RATE_LIMIT_REDIS_URL", None)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail("The test API server exited before becoming healthy.")
            try:
                with urllib.request.urlopen(base_url + "/api/health/ready", timeout=1) as response:
                    if response.status == 200:
                        break
            except (urllib.error.URLError, TimeoutError, OSError):
                time.sleep(0.25)
        else:
            pytest.fail("The test API server did not become healthy within 25 seconds.")
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_browser_registration_and_malicious_chat_name_are_safe(live_app):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        dialogs = []
        page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
        page_errors = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.goto(live_app, wait_until="domcontentloaded")
        page.locator(".switch").filter(has_text="Create an account").click()
        student_email = f"student-{uuid.uuid4().hex[:10]}@example.com"
        page.locator('input[name="full_name"]').fill("Browser Student")
        page.locator('select[name="role"]').select_option("student")
        page.locator('input[name="email"]').fill(student_email)
        page.locator('input[name="password"]').fill("BrowserStudentPass123!")
        page.locator('input[name="department"]').fill("AI & Data Science")
        page.locator('input[name="skills"]').fill("Python, SQL")
        with page.expect_response(lambda response: response.url.endswith("/api/auth/register") and response.request.method == "POST") as registration_response:
            page.locator('#authform button[type="submit"]').click()
        response = registration_response.value
        assert response.status == 201, f"Registration failed: {response.status} {response.text()}"
        page.locator(".layout").wait_for(timeout=15000)
        assert not page_errors, f"Uncaught browser errors: {page_errors}"
        page.get_by_text("Build meaningful connections for your next career step.").wait_for(timeout=10000)
        page.get_by_role("button", name="Discover alumni").click()
        page.locator("#directoryQuery").fill("Browser")
        page.locator("#directoryDepartment").fill("AI & Data Science")
        page.locator("#directorySkill").fill("Python")
        with page.expect_response(
            lambda response: response.url.startswith(live_app + "/api/profiles?")
            and response.request.method == "GET"
        ) as directory_response:
            page.locator("#directoryForm button").click()
        directory_result = directory_response.value
        assert directory_result.status == 200, directory_result.text()
        filters = parse_qs(urlparse(directory_result.url).query)
        assert filters.get("q") == ["Browser"]
        assert filters.get("department") == ["AI & Data Science"]
        assert filters.get("skill") == ["Python"]
        page.get_by_text("No matching profiles. Try another search.").wait_for(timeout=10000)
        assert not page_errors, f"Directory search caused browser errors: {page_errors}"
        page.get_by_role("button", name="Messages").click()
        page.get_by_text("Accept a connection request to start messaging.").wait_for(timeout=10000)

        student_token = page.evaluate("localStorage.getItem('ac_token')")
        assert student_token
        headers = {"Authorization": f"Bearer {student_token}"}
        malicious_name = "');alert(document.domain);//"
        alumnus = page.request.post(live_app + "/api/auth/register", data={
            "email": f"alumni-{uuid.uuid4().hex[:10]}@example.com",
            "password": "BrowserAlumniPass123!",
            "full_name": malicious_name,
            "role": "alumni",
            "skills": ["Python", "Security"],
        })
        assert alumnus.status == 201, alumnus.text()
        alumnus_data = alumnus.json()
        admin_login = page.request.post(live_app + "/api/auth/login", data={
            "email": "browser-admin@example.com", "password": "BrowserAdminPass123!",
        })
        assert admin_login.status == 200, admin_login.text()
        admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
        verify = page.request.patch(
            live_app + f"/api/admin/users/{alumnus_data['user']['id']}/verify?verified=true",
            headers=admin_headers,
        )
        assert verify.status == 200, verify.text()
        connection = page.request.post(live_app + "/api/connections", headers=headers, data={"recipient_id": alumnus_data["user"]["id"]})
        assert connection.status == 201, connection.text()
        accepted = page.request.patch(
            live_app + f"/api/connections/{connection.json()['id']}",
            headers={"Authorization": f"Bearer {alumnus_data['access_token']}"},
            data={"status": "accepted"},
        )
        assert accepted.status == 200, accepted.text()

        page.get_by_role("button", name="Messages").click()
        malicious_button = page.locator(".chat-person")
        malicious_button.wait_for(timeout=10000)
        assert malicious_button.get_attribute("onclick") is None
        malicious_button.click()
        page.locator("#chatTitle").wait_for(timeout=5000)
        assert page.locator("#chatTitle").text_content() == malicious_name

        page.get_by_role("button", name="Community feed").click()
        try:
            page.locator("#postform").wait_for(timeout=10000)
        except Exception as exc:
            pytest.fail(f"Community feed did not render. Browser errors: {page_errors}; page text: {page.locator('body').inner_text()}; cause: {exc}")
        page.locator('input[name="title"]').fill("Browser community post")
        page.locator('textarea[name="body"]').fill("A browser checked community post for safe comments.")
        page.locator('#postform button[type="submit"]').click()
        article = page.locator("#feedPosts article").first
        article.get_by_text("Browser community post").wait_for(timeout=10000)
        article.locator('[data-action="comments"]').click()
        hostile_comment = '<img src=x onerror="alert(document.domain)">'
        article.locator(".post-comment-form textarea").fill(hostile_comment)
        with page.expect_response(lambda response: "/comments" in response.url and response.request.method == "POST") as comment_response:
            article.locator(".post-comment-form button").click()
        assert comment_response.value.status == 201, comment_response.value.text()
        page.wait_for_function("() => [...document.querySelectorAll('[data-action=comments]')].some(button => button.textContent.includes('Comments · 1'))", timeout=10000)
        article = page.locator("#feedPosts article").filter(has_text="Browser community post").first
        article.locator('[data-action="comments"]').click()
        page.wait_for_function(
            "text => document.querySelector('.post-comments')?.textContent.includes(text)",
            arg=hostile_comment,
            timeout=10000,
        )
        assert page.locator("#feedPosts img").count() == 0
        assert dialogs == []
        browser.close()
