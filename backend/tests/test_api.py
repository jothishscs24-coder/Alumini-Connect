"""API regression and workflow tests using an isolated SQLite database."""
import os
import tempfile
from pathlib import Path

_TEST_DIR = tempfile.TemporaryDirectory(prefix="alumniconnect-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_TEST_DIR.name) / 'test.db'}"
os.environ["JWT_SECRET"] = "test-secret-that-is-long-enough-for-unit-tests"
os.environ["APP_ENV"] = "test"
os.environ["ENABLE_LEGACY_DEMO_STATE"] = "true"

from fastapi.testclient import TestClient  # noqa: E402
from backend.app.main import (AppState, SessionLocal, engine, User, Profile, Connection, MentorshipRequest, Post, PostReaction, PostComment, Message, Notification, app, seed_state, _password_hash)  # noqa: E402
import pytest

@pytest.fixture(scope="session", autouse=True)
def dispose_test_database_engine():
    yield
    engine.dispose()

@pytest.fixture()
def client():
    import backend.app.main as main
    main._rate_limit_hits.clear()
    with TestClient(app) as test_client:
        with SessionLocal() as db:
            # Reset legacy demo state and normalized application tables.
            row = db.get(AppState, 1)
            if row is None: db.add(AppState(id=1, state=seed_state()))
            else: row.state = seed_state()
            # Clear dependent rows first. SQLite does not enable FK cascades by default,
            # and all tests intentionally share one isolated database file.
            for model in (Notification, Message, PostComment, PostReaction, Post, MentorshipRequest, Connection, Profile, User):
                db.query(model).delete(synchronize_session=False)
            db.commit()
        yield test_client

def register(client, email, role="student", name="Test User", **extra):
    payload = {"email": email, "password": "SecurePass123!", "full_name": name, "role": role,
               "department": "AI & Data Science", "skills": ["Python", "SQL"],
               "interests": "AI applications and mentoring", "career_goal": "Software Engineer", **extra}
    response = client.post('/api/auth/register', json=payload)
    assert response.status_code == 201, response.text
    return response.json()

def auth(token): return {"Authorization": f"Bearer {token}"}

def verify_alumni(user_id):
    with SessionLocal() as db:
        account = db.get(User, user_id)
        account.is_verified = True
        db.commit()

def test_health_and_readiness(client):
    assert client.get('/api/health').json()['status'] == 'ok'
    assert client.get('/api/health/ready').json()['database'] == 'connected'

def test_homepage_and_demo_page(client):
    assert 'AlumniConnect' in client.get('/').text
    assert '<script src="/assets/app.js" defer></script>' in client.get('/').text
    assert client.get('/assets/app.js').status_code == 200
    assert 'backendStatus' in client.get('/demo').text

def test_legacy_demo_state_still_persists(client):
    state = client.get('/api/state').json()
    state['profile']['name'] = 'Regression Test User'
    assert client.put('/api/state', json=state).status_code == 200
    assert client.get('/api/state').json()['profile']['name'] == 'Regression Test User'
    assert client.put('/api/state', json={**state, 'unexpected': True}).status_code == 422

def test_register_login_me_and_duplicate_email(client):
    result = register(client, 'student@example.com')
    assert result['user']['role'] == 'student'
    assert client.get('/api/auth/me', headers=auth(result['access_token'])).json()['email'] == 'student@example.com'
    assert client.post('/api/auth/login', json={'email':'student@example.com','password':'SecurePass123!'}).status_code == 200
    assert client.post('/api/auth/login', json={'email':'student@example.com','password':'wrong-pass'}).status_code == 401
    assert client.post('/api/auth/register', json={'email':'student@example.com','password':'SecurePass123!','full_name':'Other User','role':'student'}).status_code == 409

def test_profile_update_and_recommendations(client):
    student = register(client, 'student@example.com', name='Sam Student')
    alumni = register(client, 'alumni@example.com', role='alumni', name='Alex Alumni', company='Example Co', job_title='Software Engineer', skills=['Python','SQL','APIs'])
    verify_alumni(alumni['user']['id'])
    headers = auth(student['access_token'])
    response = client.put('/api/profiles/me', headers=headers, json={'full_name':'Sam Student','department':'AI & Data Science','graduation_year':2027,'company':'','job_title':'','skills':['Python','SQL'],'interests':'AI applications','career_goal':'Software Engineer','bio':'Looking for guidance'})
    assert response.status_code == 200
    recs = client.get('/api/recommendations', headers=headers)
    assert recs.status_code == 200 and len(recs.json()) == 1
    assert recs.json()[0]['full_name'] == 'Alex Alumni'
    assert isinstance(recs.json()[0]['match_score'], int)

def test_directory_requires_auth_and_supports_search(client):
    alumni = register(client, 'a@example.com', role='alumni', name='Alex Mentor')
    verify_alumni(alumni['user']['id'])
    student = register(client, 's@example.com')
    assert client.get('/api/profiles').status_code == 401
    response = client.get('/api/profiles?q=Alex', headers=auth(student['access_token']))
    assert response.status_code == 200 and response.json()[0]['full_name'] == 'Alex Mentor'

def test_directory_server_side_filters_and_pagination(client):
    student = register(client, 'viewer@example.com')
    first = register(client, 'first@example.com', name='First Match')
    register(client, 'second@example.com', name='Second Match')
    headers = auth(student['access_token'])

    page_one = client.get('/api/profiles?q=Match&limit=1&offset=0', headers=headers)
    page_two = client.get('/api/profiles?q=Match&limit=1&offset=1', headers=headers)
    filtered_out = client.get('/api/profiles?q=Missing&limit=1', headers=headers)

    assert page_one.status_code == page_two.status_code == 200
    assert [row['full_name'] for row in page_one.json()] == ['First Match']
    assert [row['full_name'] for row in page_two.json()] == ['Second Match']
    assert filtered_out.json() == []

def test_networking_requests_include_other_participant_identity(client):
    student = register(client, 'student@example.com', name='Sam Student')
    alumni = register(client, 'alumni@example.com', role='alumni', name='Alex Mentor')
    verify_alumni(alumni['user']['id'])
    student_headers = auth(student['access_token'])
    alumni_headers = auth(alumni['access_token'])

    connection = client.post('/api/connections', headers=student_headers, json={'recipient_id': alumni['user']['id']})
    mentorship = client.post('/api/mentorship', headers=student_headers, json={
        'alumni_id': alumni['user']['id'], 'message': 'Could you review my career plan?'
    })

    assert connection.status_code == 201 and mentorship.status_code == 201
    student_connection = client.get('/api/connections', headers=student_headers).json()[0]
    alumni_connection = client.get('/api/connections', headers=alumni_headers).json()[0]
    student_mentorship = client.get('/api/mentorship', headers=student_headers).json()[0]
    alumni_mentorship = client.get('/api/mentorship', headers=alumni_headers).json()[0]
    assert student_connection['participant']['full_name'] == 'Alex Mentor'
    assert alumni_connection['participant']['full_name'] == 'Sam Student'
    assert student_mentorship['participant']['full_name'] == 'Alex Mentor'
    assert alumni_mentorship['participant']['full_name'] == 'Sam Student'

def test_connection_mentorship_posts_notifications(client):
    student = register(client, 'student@example.com', name='Sam Student')
    alumni = register(client, 'alumni@example.com', role='alumni', name='Alex Alumni')
    verify_alumni(alumni['user']['id'])
    sh, ah = auth(student['access_token']), auth(alumni['access_token'])
    conn = client.post('/api/connections', headers=sh, json={'recipient_id':alumni['user']['id']})
    assert conn.status_code == 201
    assert client.patch(f"/api/connections/{conn.json()['id']}", headers=ah, json={'status':'accepted'}).json()['status'] == 'accepted'
    sent = client.post('/api/messages', headers=sh, json={'recipient_id':alumni['user']['id'],'body':'Hello mentor'})
    assert sent.status_code == 201
    assert client.get(f"/api/messages/{student['user']['id']}", headers=ah).json()[0]['body'] == 'Hello mentor'
    mentorship = client.post('/api/mentorship', headers=sh, json={'alumni_id':alumni['user']['id'],'message':'Could you advise on internships?'})
    assert mentorship.status_code == 201
    assert client.patch(f"/api/mentorship/{mentorship.json()['id']}", headers=ah, json={'status':'accepted'}).json()['status'] == 'accepted'
    post = client.post('/api/posts', headers=ah, json={'title':'Career advice','body':'Build projects and explain your decisions.','category':'Career Advice'})
    assert post.status_code == 201 and client.get('/api/posts', headers=sh).json()[0]['title'] == 'Career advice'
    assert len(client.get('/api/notifications', headers=sh).json()) >= 1

def test_community_reactions_comments_and_notifications(client):
    author = register(client, 'author@example.com', name='Post Author')
    reader = register(client, 'reader@example.com', name='Helpful Reader')
    author_headers, reader_headers = auth(author['access_token']), auth(reader['access_token'])
    created = client.post('/api/posts', headers=author_headers, json={
        'title': 'A useful post', 'body': 'Share your project decisions clearly.'
    })
    assert created.status_code == 201
    post_id = created.json()['id']

    assert client.get('/api/posts', headers=reader_headers).json()[0]['likes_count'] == 0
    first_like = client.put(f'/api/posts/{post_id}/reactions/liked', headers=reader_headers, json={'enabled': True})
    duplicate_like = client.put(f'/api/posts/{post_id}/reactions/liked', headers=reader_headers, json={'enabled': True})
    saved = client.put(f'/api/posts/{post_id}/reactions/bookmarked', headers=reader_headers, json={'enabled': True})
    assert first_like.json()['count'] == duplicate_like.json()['count'] == 1
    assert saved.json()['enabled'] is True

    comment = client.post(f'/api/posts/{post_id}/comments', headers=reader_headers, json={'body': '<script>still rendered as text</script>'})
    assert comment.status_code == 201
    listed = client.get('/api/posts', headers=reader_headers).json()[0]
    comments = client.get(f'/api/posts/{post_id}/comments', headers=author_headers).json()
    assert listed['liked'] is True and listed['bookmarked'] is True
    assert listed['likes_count'] == 1 and listed['comments_count'] == 1
    assert comments[0]['author'] == 'Helpful Reader'
    assert comments[0]['body'] == '<script>still rendered as text</script>'
    assert any('commented on your post' in item['message'] for item in client.get('/api/notifications', headers=author_headers).json())

    assert client.put(f'/api/posts/{post_id}/reactions/liked', headers=reader_headers, json={'enabled': False}).json()['count'] == 0
    assert client.put(f'/api/posts/{post_id}/reactions/unknown', headers=reader_headers, json={'enabled': True}).status_code == 422
    assert client.post('/api/posts/99999/comments', headers=reader_headers, json={'body': 'Not found'}).status_code == 404

def test_admin_endpoints_enforce_role(client):
    alumni = register(client, 'alumni@example.com', role='alumni', name='Alex Alumni')
    student = register(client, 'student@example.com')
    assert client.get('/api/admin/users', headers=auth(student['access_token'])).status_code == 403
    with SessionLocal() as db:
        admin = User(email='admin@example.com', password_hash=_password_hash('AdminPassword123!'), role='admin', is_verified=True)
        db.add(admin); db.flush(); db.add(Profile(user_id=admin.id, full_name='Administrator')); db.commit(); admin_id = admin.id
    login = client.post('/api/auth/login', json={'email':'admin@example.com','password':'AdminPassword123!'}).json()
    users = client.get('/api/admin/users', headers=auth(login['access_token']))
    assert users.status_code == 200
    result = client.patch(f"/api/admin/users/{alumni['user']['id']}/verify?verified=true", headers=auth(login['access_token']))
    assert result.status_code == 200 and result.json()['is_verified'] is True

def test_messages_require_accepted_connection(client):
    a = register(client, 'a@example.com')
    b = register(client, 'b@example.com')
    ah, bh = auth(a['access_token']), auth(b['access_token'])
    response = client.post('/api/messages', headers=ah, json={'recipient_id':b['user']['id'],'body':'Hello'})
    assert response.status_code == 403
    assert client.get(f"/api/messages/{b['user']['id']}", headers=ah).status_code == 403
    connection = client.post('/api/connections', headers=ah, json={'recipient_id':b['user']['id']}).json()
    assert client.patch(f"/api/connections/{connection['id']}", headers=bh, json={'status':'accepted'}).status_code == 200
    assert client.post('/api/messages', headers=ah, json={'recipient_id':b['user']['id'],'body':'Hello'}).status_code == 201
    assert len(client.get(f"/api/messages/{b['user']['id']}", headers=ah).json()) == 1

def test_only_students_can_request_mentorship(client):
    alumni = register(client, 'alumni@example.com', role='alumni', name='Alex Alumni')
    other_alumni = register(client, 'other@example.com', role='alumni', name='Taylor Alumni')
    verify_alumni(other_alumni['user']['id'])
    response = client.post('/api/mentorship', headers=auth(alumni['access_token']), json={'alumni_id':other_alumni['user']['id'],'message':'Please mentor me in my career'})
    assert response.status_code == 403


def test_legacy_demo_state_is_disabled_when_flag_is_off(client, monkeypatch):
    import backend.app.main as main
    monkeypatch.setattr(main, "ENABLE_LEGACY_DEMO_STATE", False)
    assert client.get('/api/state').status_code == 404
    assert client.put('/api/state', json=seed_state()).status_code == 404


def test_legacy_demo_state_only_available_when_explicitly_enabled(client, monkeypatch):
    import backend.app.main as main
    monkeypatch.setattr(main, "ENABLE_LEGACY_DEMO_STATE", True)
    assert client.get('/api/state').status_code == 200


def test_production_rejects_missing_or_weak_jwt_secret():
    import subprocess, sys
    env = dict(os.environ)
    env.pop("JWT_SECRET", None)
    env["APP_ENV"] = "production"
    result = subprocess.run([sys.executable, "-c", "import backend.app.main"], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "requires a securely generated, unique JWT_SECRET" in result.stderr


def test_frontend_message_handler_does_not_interpolate_names_into_inline_javascript():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "frontend"
    html = (root / "index.html").read_text()
    script = (root / "app.js").read_text()
    assert 'class="personline chat-person"' in script
    assert "button.dataset.chatName" in script
    assert 'onclick="openChat(' not in html + script
    assert 'onclick=' not in html

@pytest.mark.parametrize('environment', ['production', 'prod', 'staging', 'stage'])
def test_deployment_environments_reject_missing_jwt_secret(environment):
    import subprocess, sys
    env = dict(os.environ)
    env.pop('JWT_SECRET', None)
    env['APP_ENV'] = environment
    env['DATABASE_URL'] = 'sqlite://'
    result = subprocess.run([sys.executable, '-c', 'import backend.app.main'], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'requires a securely generated, unique JWT_SECRET' in result.stderr


@pytest.mark.parametrize('weak_secret', [
    'a' * 64,
    '01234567890123456789012345678901',
    'replace_with_a_long_random_signing_secret_at_least_32_chars',
    'changeme-' + 'x' * 40,
])
def test_deployment_environments_reject_predictable_jwt_secrets(weak_secret):
    import subprocess, sys
    env = dict(os.environ)
    env['JWT_SECRET'] = weak_secret
    env['APP_ENV'] = 'prod'
    env['DATABASE_URL'] = 'sqlite://'
    result = subprocess.run([sys.executable, '-c', 'import backend.app.main'], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'requires a securely generated, unique JWT_SECRET' in result.stderr


def test_production_accepts_securely_generated_jwt_secret():
    import subprocess, sys, secrets
    env = dict(os.environ)
    env['JWT_SECRET'] = secrets.token_urlsafe(48)
    env['APP_ENV'] = 'production'
    env['DATABASE_URL'] = 'sqlite://'
    result = subprocess.run([sys.executable, '-c', 'import backend.app.main; print("CONFIG_OK")'], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'CONFIG_OK' in result.stdout


def test_unknown_environment_fails_closed():
    import subprocess, sys
    env = dict(os.environ)
    env['APP_ENV'] = 'prodction'
    env['DATABASE_URL'] = 'sqlite://'
    result = subprocess.run([sys.executable, '-c', 'import backend.app.main'], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'Unsupported APP_ENV' in result.stderr


def test_recommendations_only_return_verified_active_alumni(client):
    student = register(client, 'student-recs@example.com', name='Student')
    verified = register(client, 'verified-recs@example.com', role='alumni', name='Verified Alum')
    unverified = register(client, 'unverified-recs@example.com', role='alumni', name='Unverified Alum')
    verify_alumni(verified['user']['id'])
    inactive = register(client, 'inactive-recs@example.com', role='alumni', name='Inactive Alum')
    verify_alumni(inactive['user']['id'])
    with SessionLocal() as db:
        db.get(User, inactive['user']['id']).is_active = False
        db.commit()
    response = client.get('/api/recommendations', headers=auth(student['access_token']))
    assert response.status_code == 200
    names = {item['full_name'] for item in response.json()}
    assert 'Verified Alum' in names
    assert 'Unverified Alum' not in names
    assert 'Inactive Alum' not in names
    assert all(item['role'] == 'alumni' and item['is_verified'] for item in response.json())


def test_unverified_alumni_hidden_from_directory_and_direct_profile(client):
    student = register(client, 'directory-student@example.com')
    alumni = register(client, 'hidden-alumni@example.com', role='alumni', name='Hidden Alumni')
    headers = auth(student['access_token'])
    assert client.get('/api/profiles?role=alumni', headers=headers).json() == []
    assert client.get(f"/api/profiles/{alumni['user']['id']}", headers=headers).status_code == 404
    verify_alumni(alumni['user']['id'])
    assert len(client.get('/api/profiles?role=alumni', headers=headers).json()) == 1
    assert client.get(f"/api/profiles/{alumni['user']['id']}", headers=headers).status_code == 200


def test_unverified_alumni_cannot_receive_connection_or_mentorship_requests(client):
    student = register(client, 'policy-student@example.com')
    alumni = register(client, 'policy-alumni@example.com', role='alumni', name='Not Yet Verified')
    headers = auth(student['access_token'])
    connection = client.post('/api/connections', headers=headers, json={'recipient_id': alumni['user']['id']})
    mentorship = client.post('/api/mentorship', headers=headers, json={'alumni_id': alumni['user']['id'], 'message': 'Please help with career guidance'})
    assert connection.status_code == 404
    assert mentorship.status_code == 404


def test_unverified_alumni_cannot_initiate_networking_or_send_messages(client):
    unverified = register(client, 'unverified-sender@example.com', role='alumni', name='Unverified Sender')
    student = register(client, 'network-student@example.com')
    headers = auth(unverified['access_token'])
    assert client.post('/api/connections', headers=headers, json={'recipient_id': student['user']['id']}).status_code == 403
    assert client.post('/api/messages', headers=headers, json={'recipient_id': student['user']['id'], 'body': 'Hello'}).status_code == 403


def test_unverified_alumni_cannot_access_existing_message_thread(client):
    student = register(client, 'thread-student@example.com')
    alumni = register(client, 'thread-alumni@example.com', role='alumni', name='Verified Initially')
    verify_alumni(alumni['user']['id'])
    sh, ah = auth(student['access_token']), auth(alumni['access_token'])
    connection = client.post('/api/connections', headers=sh, json={'recipient_id': alumni['user']['id']}).json()
    assert client.patch(f"/api/connections/{connection['id']}", headers=ah, json={'status': 'accepted'}).status_code == 200
    assert client.post('/api/messages', headers=sh, json={'recipient_id': alumni['user']['id'], 'body': 'Hello there'}).status_code == 201
    with SessionLocal() as db:
        db.get(User, alumni['user']['id']).is_verified = False
        db.commit()
    assert client.get(f"/api/messages/{student['user']['id']}", headers=ah).status_code == 403
    assert client.post('/api/messages', headers=ah, json={'recipient_id': student['user']['id'], 'body': 'After revocation'}).status_code == 403

@pytest.mark.parametrize('params', [
    {'department': 'd' * 101},
    {'skill': 's' * 81},
    {'q': 'q' * 121},
    {'role': 'admin'},
    {'limit': 101},
    {'offset': 1_000_001},
])
def test_profile_search_rejects_out_of_policy_parameters(client, params):
    user = register(client, 'bounded-search@example.com')
    response = client.get('/api/profiles', params=params, headers=auth(user['access_token']))
    assert response.status_code == 422


def test_registration_rejects_oversized_individual_skill(client):
    response = client.post('/api/auth/register', json={
        'email': 'oversized-skill@example.com', 'password': 'SecurePass123!',
        'full_name': 'Skill Tester', 'role': 'student', 'skills': ['x' * 81],
    })
    assert response.status_code == 422


def test_registration_accepts_skill_at_maximum_length(client):
    response = client.post('/api/auth/register', json={
        'email': 'max-skill@example.com', 'password': 'SecurePass123!',
        'full_name': 'Skill Tester', 'role': 'student', 'skills': ['x' * 80],
    })
    assert response.status_code == 201


def test_admin_profiles_are_not_exposed_in_public_directory(client):
    student = register(client, 'profile-viewer@example.com')
    with SessionLocal() as db:
        admin = User(email='private-admin@example.com', password_hash=_password_hash('AdminPassword123!'), role='admin', is_verified=True)
        db.add(admin); db.flush(); db.add(Profile(user_id=admin.id, full_name='Private Administrator')); db.commit()
        admin_id = admin.id
    headers = auth(student['access_token'])
    assert client.get('/api/profiles?role=admin', headers=headers).status_code == 422
    assert client.get(f'/api/profiles/{admin_id}', headers=headers).status_code == 404


def test_login_rate_limit_returns_429_after_threshold(client):
    responses = [client.post('/api/auth/login', json={'email': 'nobody@example.com', 'password': 'wrong-password'}) for _ in range(6)]
    assert [response.status_code for response in responses[:5]] == [401] * 5
    assert responses[5].status_code == 429
    assert responses[5].headers.get('retry-after') == '60'
    assert responses[5].headers.get('x-content-type-options') == 'nosniff'
    assert responses[5].json()['detail'] == 'Too many requests. Please try again later.'


def test_rate_limit_is_scoped_by_route_and_client(client):
    responses = [client.post('/api/auth/login', json={'email': 'nobody@example.com', 'password': 'wrong-password'}) for _ in range(5)]
    assert all(response.status_code == 401 for response in responses)
    # A different protected route has an independent bucket.
    student = register(client, 'rate-limit-scope@example.com')
    assert client.get('/api/recommendations', headers=auth(student['access_token'])).status_code == 200


def test_security_headers_are_present(client):
    response = client.get('/api/health')
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert response.headers['x-frame-options'] == 'DENY'
    assert response.headers['referrer-policy'] == 'strict-origin-when-cross-origin'
    assert response.headers['permissions-policy'] == 'camera=(), microphone=(), geolocation=()'
    assert response.headers['cross-origin-opener-policy'] == 'same-origin'
    assert response.headers['cross-origin-resource-policy'] == 'same-origin'

def test_production_csp_serves_external_app_script_and_hides_demo(client, monkeypatch):
    import backend.app.main as main
    monkeypatch.setattr(main, 'APP_ENV', 'production')
    home = client.get('/')
    csp = home.headers.get('content-security-policy', '')
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert '<script src="/assets/app.js" defer></script>' in home.text
    assert '<script>' not in home.text and 'onclick=' not in home.text
    script = client.get('/assets/app.js')
    assert script.status_code == 200 and "document.addEventListener('click'" in script.text
    assert script.headers.get('content-security-policy') == csp
    assert client.get('/demo').status_code == 404
    assert client.get('/assets/demo.html').status_code == 404


def test_deployment_requires_shared_rate_limit_store_at_startup():
    import subprocess, sys, secrets
    env = dict(os.environ)
    env['APP_ENV'] = 'prod'
    env['JWT_SECRET'] = secrets.token_urlsafe(48)
    env.pop('RATE_LIMIT_REDIS_URL', None)
    env['DATABASE_URL'] = 'sqlite://'
    code = "import asyncio\nfrom backend.app.main import app, lifespan\nasync def run():\n    async with lifespan(app):\n        pass\nasyncio.run(run())\n"
    result = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'requires RATE_LIMIT_REDIS_URL' in result.stderr


def test_api_docs_are_disabled_by_default_in_production():
    import subprocess, sys, secrets
    env = dict(os.environ)
    env['APP_ENV'] = 'production'
    env['JWT_SECRET'] = secrets.token_urlsafe(48)
    env['RATE_LIMIT_REDIS_URL'] = 'redis://localhost:6379/0'
    env.pop('ENABLE_API_DOCS', None)
    env['DATABASE_URL'] = 'sqlite://'
    result = subprocess.run([sys.executable, '-c', 'from backend.app.main import app; print(app.docs_url, app.openapi_url)'], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'None None' in result.stdout


def test_ai_health_reports_model_state_without_triggering_download(client):
    import backend.app.main as main
    main._embedding_model = None
    main._embedding_retry_after = 0.0
    response = client.get('/api/health/ai')
    assert response.status_code == 200
    assert response.json()['status'] == 'not_loaded'
    assert response.json()['semantic_embeddings_active'] is False
    assert response.json()['fallback_available'] is True


def test_embedding_loader_retries_after_failure(monkeypatch):
    import sys, types
    import backend.app.main as main
    main._embedding_model = None
    main._embedding_load_error = None
    main._embedding_retry_after = 0.0
    # Simulate a first model load failure, then a successful load on the next retry.
    class BrokenModel:
        def __init__(self, name):
            raise OSError('simulated unavailable model')
    monkeypatch.setitem(sys.modules, 'sentence_transformers', types.SimpleNamespace(SentenceTransformer=BrokenModel))
    assert main._embed('Python machine learning') is None
    assert main._embedding_retry_after > 0
    class FakeVector:
        def tolist(self): return [0.25, 0.75]
    class WorkingModel:
        def __init__(self, name): pass
        def encode(self, text, normalize_embeddings=True): return FakeVector()
    monkeypatch.setitem(sys.modules, 'sentence_transformers', types.SimpleNamespace(SentenceTransformer=WorkingModel))
    main._embedding_retry_after = 0.0
    assert main._embed('Python machine learning') == [0.25, 0.75]
    assert main._embedding_model is not None
    main._embedding_model = None
    main._embedding_load_error = None
    main._embedding_retry_after = 0.0


def test_initial_alembic_migration_up_and_down(tmp_path):
    import subprocess, sys
    database = tmp_path / 'migration.db'
    env = dict(os.environ)
    env['DATABASE_URL'] = f'sqlite:///{database}'
    env['APP_ENV'] = 'test'
    root = Path(__file__).resolve().parents[2]
    up = subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], cwd=root, env=env, capture_output=True, text=True)
    assert up.returncode == 0, up.stderr
    import sqlite3
    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {'alembic_version', 'users', 'profiles', 'connections', 'mentorship_requests', 'messages', 'notifications', 'posts', 'post_reactions', 'post_comments', 'app_state'} <= tables
    down = subprocess.run([sys.executable, '-m', 'alembic', 'downgrade', 'base'], cwd=root, env=env, capture_output=True, text=True)
    assert down.returncode == 0, down.stderr
    with sqlite3.connect(database) as connection:
        tables_after = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert 'users' not in tables_after


def test_rate_limiter_fails_closed_if_shared_store_fails_in_production(client, monkeypatch):
    import backend.app.main as main
    class BrokenRedis:
        async def eval(self, *args, **kwargs):
            raise ConnectionError('simulated Redis outage')
    monkeypatch.setattr(main, 'APP_ENV', 'production')
    main.app.state.rate_limit_redis = BrokenRedis()
    response = client.post('/api/auth/login', json={'email': 'someone@example.com', 'password': 'wrong'})
    assert response.status_code == 503
    assert response.json()['detail'] == 'Request protection is temporarily unavailable.'
    main.app.state.rate_limit_redis = None


def test_unverified_alumni_cannot_browse_networking_or_recommendations(client):
    alumni = register(client, 'blocked-alumni@example.com', role='alumni', name='Unverified Alumni')
    headers = auth(alumni['access_token'])
    assert client.get('/api/recommendations', headers=headers).status_code == 403
    assert client.get('/api/profiles', headers=headers).status_code == 403


def test_admin_cannot_use_networking_actions(client):
    with SessionLocal() as db:
        admin = User(email='network-admin@example.com', password_hash=_password_hash('AdminPassword123!'), role='admin', is_verified=True)
        db.add(admin); db.flush(); db.add(Profile(user_id=admin.id, full_name='Admin Account')); db.commit()
    login = client.post('/api/auth/login', json={'email': 'network-admin@example.com', 'password': 'AdminPassword123!'}).json()
    headers = auth(login['access_token'])
    assert client.get('/api/recommendations', headers=headers).status_code == 403
    assert client.post('/api/connections', headers=headers, json={'recipient_id': 9999}).status_code == 403


def test_verification_revocation_blocks_mentorship_response(client):
    student = register(client, 'revocation-student@example.com')
    alumni = register(client, 'revocation-alumni@example.com', role='alumni', name='Verified Mentor')
    verify_alumni(alumni['user']['id'])
    request = client.post('/api/mentorship', headers=auth(student['access_token']), json={
        'alumni_id': alumni['user']['id'], 'message': 'Please advise me on career planning'
    })
    assert request.status_code == 201
    with SessionLocal() as db:
        db.get(User, alumni['user']['id']).is_verified = False
        db.commit()
    response = client.patch(f"/api/mentorship/{request.json()['id']}", headers=auth(alumni['access_token']), json={'status': 'accepted'})
    assert response.status_code == 403


def test_registration_rejects_client_supplied_server_managed_fields(client):
    response = client.post('/api/auth/register', json={
        'email': 'spoof-verified@example.com', 'password': 'SecurePass123!',
        'full_name': 'Spoof Attempt', 'role': 'alumni', 'is_verified': True,
        'is_active': True,
    })
    assert response.status_code == 422


def test_oversized_declared_request_body_is_rejected(client):
    response = client.post('/api/auth/login', content=b'x' * 1_000_001, headers={'Content-Type': 'application/json'})
    assert response.status_code == 413
    assert response.headers.get('x-content-type-options') == 'nosniff'


def test_production_readiness_fails_if_redis_is_unavailable(client, monkeypatch):
    import backend.app.main as main
    class BrokenRedis:
        async def ping(self):
            raise ConnectionError('simulated Redis outage')
    monkeypatch.setattr(main, 'APP_ENV', 'production')
    main.app.state.rate_limit_redis = BrokenRedis()
    response = client.get('/api/health/ready')
    assert response.status_code == 503
    assert response.json()['detail'] == 'Shared rate-limit storage is unavailable'
    main.app.state.rate_limit_redis = None
