"""AlumniConnect API: legacy demo-state compatibility plus normalized production-style APIs."""
import hashlib
import hmac
import json
import os
import secrets
import time
from collections import defaultdict, deque
from threading import Lock
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Literal, Optional

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, create_engine, func, select, or_
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

BASE_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIR = BASE_DIR / "frontend"

def normalize_database_url(url: str) -> str:
    """Select the installed Psycopg 3 driver for PostgreSQL URLs."""
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url

DATABASE_URL = normalize_database_url(
    os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'backend' / 'data' / 'alumniconnect.db'}")
)
# Normalize deployment aliases before applying security-sensitive configuration.
_RAW_APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
_ENV_ALIASES = {
    "dev": "development",
    "local": "development",
    "prod": "production",
    "stage": "staging",
}
APP_ENV = _ENV_ALIASES.get(_RAW_APP_ENV, _RAW_APP_ENV)
_ALLOWED_APP_ENVS = {"development", "test", "staging", "production"}
if APP_ENV not in _ALLOWED_APP_ENVS:
    raise RuntimeError(
        f"Unsupported APP_ENV '{_RAW_APP_ENV}'. Use development, test, staging, or production."
    )

_configured_jwt_secret = os.getenv("JWT_SECRET", "").strip()
_PLACEHOLDER_JWT_SECRETS = {
    "dev-only-change-this-secret-before-deployment",
    "changeme",
    "change-me",
    "secret",
    "password",
    "replace_with_a_long_random_signing_secret_at_least_32_chars",
    "your-secret-key",
    "your_jwt_secret",
}

def _jwt_secret_is_acceptable(value: str) -> bool:
    """Reject missing, known-placeholder, low-diversity and obviously patterned keys.

    A length check alone is not a reliable measure of secret strength. Production
    secrets should be generated with a cryptographically secure random generator.
    """
    candidate = value.strip()
    if len(candidate) < 32 or candidate.lower() in _PLACEHOLDER_JWT_SECRETS:
        return False
    if len(set(candidate)) < 12:
        return False
    lowered = candidate.lower()
    if any(pattern in lowered for pattern in ("replace_with", "example_secret", "your_secret", "changeme")):
        return False
    # Reject common repetition/pattern mistakes even when the string is long.
    if len(set(candidate)) <= 2 or candidate == candidate[:1] * len(candidate):
        return False
    return True

if APP_ENV not in {"development", "test"} and not _jwt_secret_is_acceptable(_configured_jwt_secret):
    raise RuntimeError(
        "Staging/production requires a securely generated, unique JWT_SECRET "
        "(at least 32 characters with sufficient character diversity)."
    )
# Ephemeral secrets are convenient for local development only. They invalidate all
# tokens on restart, so deployment environments must supply a stable secret.
JWT_SECRET = _configured_jwt_secret or secrets.token_urlsafe(48)
ENABLE_LEGACY_DEMO_STATE = os.getenv("ENABLE_LEGACY_DEMO_STATE", "false").strip().lower() in {"1", "true", "yes", "on"}
RATE_LIMIT_REDIS_URL = os.getenv("RATE_LIMIT_REDIS_URL", "").strip()
_docs_default = "true" if APP_ENV in {"development", "test"} else "false"
ENABLE_API_DOCS = os.getenv("ENABLE_API_DOCS", _docs_default).strip().lower() in {"1", "true", "yes", "on"}
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_MINUTES = int(os.getenv("ACCESS_TOKEN_MINUTES", "60"))
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

class Base(DeclarativeBase): pass

# Legacy UI-state storage retained so the supplied prototype continues to run.
class AppState(Base):
    __tablename__ = "app_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="student", nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class Profile(Base):
    __tablename__ = "profiles"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    department: Mapped[str] = mapped_column(String(160), default="")
    graduation_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    company: Mapped[str] = mapped_column(String(160), default="")
    job_title: Mapped[str] = mapped_column(String(160), default="")
    skills: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    interests: Mapped[str] = mapped_column(Text, default="")
    career_goal: Mapped[str] = mapped_column(String(240), default="")
    bio: Mapped[str] = mapped_column(Text, default="")
    embedding: Mapped[Optional[list[float]]] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class Connection(Base):
    __tablename__ = "connections"
    __table_args__ = (UniqueConstraint("requester_id", "recipient_id", name="uq_connection_pair"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    recipient_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class MentorshipRequest(Base):
    __tablename__ = "mentorship_requests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    alumni_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class Post(Base):
    __tablename__ = "posts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(80), default="General")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class PostReaction(Base):
    __tablename__ = "post_reactions"
    __table_args__ = (UniqueConstraint("post_id", "user_id", "kind", name="uq_post_reaction"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)

class PostComment(Base):
    __tablename__ = "post_comments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    recipient_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class StatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alumni: list[dict[str, Any]]
    posts: list[dict[str, Any]]
    requests: list[dict[str, Any]]
    liked: list[int]
    bookmarks: list[int]
    tasks: list[bool]
    messages: dict[str, list[dict[str, Any]]]
    verified: dict[str, bool]
    profile: dict[str, Any]

class StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

class RegisterPayload(StrictPayload):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=2, max_length=160)
    role: str = Field(pattern="^(student|alumni)$")
    department: str = Field(default="", max_length=160)
    graduation_year: Optional[int] = Field(default=None, ge=1950, le=2100)
    company: str = Field(default="", max_length=160)
    job_title: str = Field(default="", max_length=160)
    skills: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(default_factory=list, max_length=40)
    interests: str = Field(default="", max_length=3000)
    career_goal: str = Field(default="", max_length=240)
    bio: str = Field(default="", max_length=3000)

class LoginPayload(StrictPayload):
    email: EmailStr
    password: str

class ProfilePayload(StrictPayload):
    full_name: str = Field(min_length=2, max_length=160)
    department: str = Field(default="", max_length=160)
    graduation_year: Optional[int] = Field(default=None, ge=1950, le=2100)
    company: str = Field(default="", max_length=160)
    job_title: str = Field(default="", max_length=160)
    skills: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(default_factory=list, max_length=40)
    interests: str = Field(default="", max_length=3000)
    career_goal: str = Field(default="", max_length=240)
    bio: str = Field(default="", max_length=3000)

class ConnectionPayload(StrictPayload): recipient_id: int
class MentorshipPayload(StrictPayload): alumni_id: int; message: str = Field(min_length=5, max_length=2000)
class StatusPayload(StrictPayload): status: str = Field(pattern="^(accepted|rejected)$")
class PostPayload(StrictPayload): title: str = Field(min_length=3, max_length=240); body: str = Field(min_length=5, max_length=10000); category: str = Field(default="General", max_length=80)
class PostReactionPayload(StrictPayload): enabled: bool
class PostCommentPayload(StrictPayload): body: str = Field(min_length=1, max_length=3000)
class MessagePayload(StrictPayload): recipient_id: int; body: str = Field(min_length=1, max_length=5000)

# Local, real semantic embeddings. The model loads only when recommendations are requested.
_embedding_model = None
_embedding_load_error = None
_embedding_retry_after = 0.0
_EMBEDDING_RETRY_SECONDS = 60.0
def _embed(text: str) -> Optional[list[float]]:
    """Try local semantic embeddings; retry model-load failures after a cooldown."""
    global _embedding_model, _embedding_load_error, _embedding_retry_after
    import time
    try:
        if _embedding_model is None and time.monotonic() >= _embedding_retry_after:
            try:
                from sentence_transformers import SentenceTransformer
                _embedding_model = SentenceTransformer(os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"))
                _embedding_load_error = None
                _embedding_retry_after = 0.0
            except Exception as exc:  # offline/missing model should not break the API
                _embedding_load_error = f"{type(exc).__name__}: {exc}"
                _embedding_retry_after = time.monotonic() + _EMBEDDING_RETRY_SECONDS
                return None
        if _embedding_model is not None:
            vector = _embedding_model.encode(text, normalize_embeddings=True)
            return [float(v) for v in vector.tolist()]
    except Exception as exc:
        # Encoding can fail independently of model loading; retain fallback and retry later.
        _embedding_load_error = f"{type(exc).__name__}: {exc}"
        _embedding_retry_after = time.monotonic() + _EMBEDDING_RETRY_SECONDS
        _embedding_model = None
    return None

def _profile_text(p: Profile) -> str:
    return " ".join([p.full_name, p.department, str(p.graduation_year or ""), p.company, p.job_title,
                     " ".join(p.skills or []), p.interests, p.career_goal, p.bio])

def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b): return 0.0
    # vectors from SentenceTransformer are normalized, but calculate robustly anyway
    dot = sum(x*y for x, y in zip(a, b)); na = sum(x*x for x in a) ** .5; nb = sum(y*y for y in b) ** .5
    return dot / (na * nb) if na and nb else 0.0

def _fallback_similarity(a: str, b: str) -> float:
    # Transparent fallback if the embedding model cannot be downloaded; embeddings remain preferred.
    import re
    stop = {"the", "and", "for", "with", "from", "into", "that", "this", "are", "you", "your", "can", "help"}
    wa = {w for w in re.findall(r"[a-z0-9+#.]+", a.lower()) if len(w) > 1 and w not in stop}
    wb = {w for w in re.findall(r"[a-z0-9+#.]+", b.lower()) if len(w) > 1 and w not in stop}
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0

def _password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310000)
    import base64
    return "pbkdf2_sha256$310000$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(digest).decode()

def _password_ok(password: str, encoded: str) -> bool:
    try:
        import base64
        algorithm, iterations, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256": return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.urlsafe_b64decode(salt), int(iterations))
        return hmac.compare_digest(base64.urlsafe_b64encode(digest).decode(), expected)
    except (ValueError, TypeError): return False

def _token(user: User) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": str(user.id), "role": user.role, "iat": now, "exp": now + timedelta(minutes=ACCESS_TOKEN_MINUTES)}, JWT_SECRET, algorithm=JWT_ALGORITHM)

def _public_user(db: Session, user: User) -> dict[str, Any]:
    p = db.get(Profile, user.id)
    return {"id": user.id, "email": user.email, "role": user.role, "is_verified": user.is_verified,
            "profile": _profile_dict(p) if p else None}

def _profile_dict(p: Profile) -> dict[str, Any]:
    return {"user_id": p.user_id, "full_name": p.full_name, "department": p.department,
            "graduation_year": p.graduation_year, "company": p.company, "job_title": p.job_title,
            "skills": p.skills or [], "interests": p.interests, "career_goal": p.career_goal, "bio": p.bio}

def _network_participant(db: Session, user_id: int) -> Optional[dict[str, Any]]:
    """Return the public identity fields needed to render a networking request."""
    row = db.execute(select(User, Profile).join(Profile, Profile.user_id == User.id).where(User.id == user_id)).first()
    if not row:
        return None
    account, profile = row
    if not account.is_active or (account.role == "alumni" and not account.is_verified):
        return None
    return {
        "user_id": account.id,
        "full_name": profile.full_name,
        "role": account.role,
        "company": profile.company,
        "job_title": profile.job_title,
        "is_verified": account.is_verified,
    }

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

def current_user(authorization: Optional[str] = Header(default=None), db: Session = Depends(get_db)) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Bearer access token required")
    try:
        payload = jwt.decode(authorization.split(" ", 1)[1], JWT_SECRET, algorithms=[JWT_ALGORITHM])
        uid = int(payload["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(status_code=401, detail="Invalid or expired access token")
    user = db.get(User, uid)
    if not user or not user.is_active: raise HTTPException(status_code=401, detail="Account is unavailable")
    return user

def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin": raise HTTPException(status_code=403, detail="Administrator access required")
    return user

def _eligible_alumnus(user: Optional[User]) -> bool:
    """Return whether an account is currently eligible for alumni-only features."""
    return bool(user and user.is_active and user.role == "alumni" and user.is_verified)

def _ensure_network_eligible(user: User) -> None:
    """Enforce account eligibility at the API boundary for networking actions."""
    if user.role == "admin":
        raise HTTPException(status_code=403, detail="Administrator accounts cannot use networking features")
    if user.role == "alumni" and not _eligible_alumnus(user):
        raise HTTPException(status_code=403, detail="Alumni verification is required to use networking features")

def _notify(db: Session, user_id: int, message: str):
    db.add(Notification(user_id=user_id, message=message))

def seed_state() -> dict[str, Any]:
    # Existing prototype sample content retained for backward compatibility.
    return {
        "alumni": [
            {"id": 1, "name": "Priya Raman", "role": "Product Manager", "company": "Zoho", "dept": "Computer Science & Engineering", "grad": "2019", "domain": "Product", "skills": ["Product Strategy", "SQL", "Communication"], "match": 96, "verified": True, "bio": "Helps students understand product thinking, internships, and early-career decisions.", "initials": "PR", "requests": 0},
            {"id": 2, "name": "Arjun Kumar", "role": "Senior Software Engineer", "company": "Freshworks", "dept": "Computer Science & Engineering", "grad": "2020", "domain": "Software Engineering", "skills": ["Java", "Spring Boot", "System Design"], "match": 93, "verified": True, "bio": "Enjoys mentoring students on backend development, DSA, and interview preparation.", "initials": "AK", "requests": 0},
            {"id": 3, "name": "Meera Krishnan", "role": "Machine Learning Engineer", "company": "AI Research Team", "dept": "Information Technology", "grad": "2021", "domain": "AI / ML", "skills": ["Python", "PyTorch", "NLP"], "match": 89, "verified": True, "bio": "Shares practical advice on ML projects, evaluation, and getting started in applied AI.", "initials": "MK", "requests": 0},
            {"id": 4, "name": "Vikram S", "role": "Data Analyst", "company": "Accenture", "dept": "Computer Science & Engineering", "grad": "2018", "domain": "Data", "skills": ["SQL", "Python", "Power BI"], "match": 86, "verified": True, "bio": "Mentors on analytics portfolios, SQL interviews, and communicating insights.", "initials": "VS", "requests": 0},
            {"id": 5, "name": "Divya N", "role": "Security Analyst", "company": "Technology Services", "dept": "Electronics & Communication", "grad": "2022", "domain": "Cybersecurity", "skills": ["Security Basics", "Linux", "Networking"], "match": 78, "verified": False, "bio": "Interested in helping students explore security fundamentals and certifications.", "initials": "DN", "requests": 0},
            {"id": 6, "name": "Rahul Menon", "role": "Software Engineer", "company": "Product Startup", "dept": "Computer Science & Engineering", "grad": "2022", "domain": "Software Engineering", "skills": ["Python", "APIs", "PostgreSQL"], "match": 84, "verified": True, "bio": "Can help students ship portfolio projects and understand engineering workflows.", "initials": "RM", "requests": 0}],
        "posts": [
            {"id": 1, "author": "Priya Raman", "role": "Product Manager · Zoho", "initials": "PR", "category": "Hiring Trends", "title": "What makes an entry-level candidate stand out?", "body": "A clear project explanation is often more useful than a long list of tools. Be ready to explain the problem, trade-offs, how you tested your work, and what you would improve next. Students: practice a two-minute walkthrough of one project.", "time": "2 hours ago", "likes": 18, "source": "", "sample": True},
            {"id": 2, "author": "Arjun Kumar", "role": "Senior Software Engineer · Freshworks", "initials": "AK", "category": "Skills & Learning", "title": "A practical backend preparation checklist", "body": "Focus on one language, data structures, HTTP fundamentals, SQL, and a small API project with tests. Depth beats collecting frameworks. Keep your README clear enough that someone can run your project without asking you for instructions.", "time": "Yesterday", "likes": 26, "source": "", "sample": True},
            {"id": 3, "author": "Meera Krishnan", "role": "Machine Learning Engineer", "initials": "MK", "category": "Technology", "title": "For ML projects, evaluate more than accuracy", "body": "Document your baseline, data split, class balance, error cases, and the metric that matches your actual objective. A model demo becomes more credible when you can explain where it fails and how you would monitor it.", "time": "2 days ago", "likes": 31, "source": "", "sample": True}],
        "requests": [{"id": 1, "student": "Karthik Subramanian", "studentInitials": "KS", "alumniId": 1, "purpose": "Guidance on Java backend and placement preparation", "status": "pending", "date": "Today"}],
        "liked": [], "bookmarks": [], "tasks": [False, False, False], "messages": {"1": [{"mine": False, "text": "Hi Karthik! What are you currently working on?"}, {"mine": True, "text": "I am building projects and preparing for backend roles."}, {"mine": False, "text": "Good start. Be ready to explain one project end to end."}], "2": [{"mine": False, "text": "Happy to share some advice on your learning roadmap."}]}, "verified": {"5": False}, "profile": {"name": "Karthik Subramanian", "dept": "Computer Science & Engineering", "cgpa": "8.2", "goal": "Software Developer", "skills": "Python, Java, SQL, Machine Learning", "about": "Computer Science student exploring software engineering and AI. Looking to learn from alumni and build practical projects."}}

@asynccontextmanager
async def lifespan(app: FastAPI):
    (BASE_DIR / "backend" / "data").mkdir(parents=True, exist_ok=True)
    # Production schema changes are managed by Alembic before the app starts.
    # create_all remains a convenience for local development and isolated tests.
    if APP_ENV in {"development", "test"}:
        Base.metadata.create_all(bind=engine)
    # Optional first-admin bootstrap. Never hard-code an administrator password.
    admin_email, admin_password = os.getenv("ADMIN_EMAIL"), os.getenv("ADMIN_PASSWORD")
    if admin_email and admin_password:
        with SessionLocal() as db:
            existing = db.scalar(select(User).where(User.email == admin_email.lower()))
            if existing is None:
                admin = User(email=admin_email.lower(), password_hash=_password_hash(admin_password), role="admin", is_verified=True)
                db.add(admin); db.flush()
                db.add(Profile(user_id=admin.id, full_name="Platform Administrator")); db.commit()
    redis_client = None
    if APP_ENV not in {"development", "test"}:
        if not RATE_LIMIT_REDIS_URL:
            raise RuntimeError("Staging/production requires RATE_LIMIT_REDIS_URL for shared rate limiting.")
        try:
            from redis.asyncio import from_url as redis_from_url
            redis_client = redis_from_url(RATE_LIMIT_REDIS_URL, encoding="utf-8", decode_responses=True)
            await redis_client.ping()
        except Exception as exc:
            if redis_client is not None:
                await redis_client.aclose()
            raise RuntimeError("Shared rate-limit storage is unavailable; refusing deployment startup.") from exc
    app.state.rate_limit_redis = redis_client
    with SessionLocal() as db:
        row = db.get(AppState, 1)
        if row is None: db.add(AppState(id=1, state=seed_state())); db.commit()
    try:
        yield
    finally:
        if redis_client is not None:
            await redis_client.aclose()

app = FastAPI(
    title="AlumniConnect API",
    description="Alumni networking, mentorship, messaging and semantic recommendations API.",
    version="1.0.0", lifespan=lifespan,
    docs_url="/docs" if ENABLE_API_DOCS else None,
    redoc_url="/redoc" if ENABLE_API_DOCS else None,
    openapi_url="/openapi.json" if ENABLE_API_DOCS else None,
)

# High-risk endpoints use shared Redis sliding-window limits in deployment. A small
# process-local limiter is retained only for local development and isolated tests.
_RATE_LIMIT_RULES = {
    ("POST", "/api/auth/login"): (5, 60),
    ("POST", "/api/auth/register"): (10, 60),
    ("GET", "/api/recommendations"): (30, 60),
    ("POST", "/api/messages"): (30, 60),
    ("POST", "/api/connections"): (30, 60),
    ("POST", "/api/mentorship"): (20, 60),
}
_rate_limit_hits: dict[str, deque[float]] = defaultdict(deque)
_rate_limit_lock = Lock()

async def _rate_limit_exceeded(request, bucket: str, limit: int, window_seconds: int) -> bool:
    now = time.time()
    client_host = request.client.host if request.client else "unknown"
    key = f"alumniconnect:ratelimit:{bucket}:{client_host}"
    redis_client = getattr(request.app.state, "rate_limit_redis", None)
    if redis_client is not None:
        script = """
        local now = tonumber(ARGV[1])
        local window = tonumber(ARGV[2])
        local limit = tonumber(ARGV[3])
        redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
        local count = redis.call('ZCARD', KEYS[1])
        if count >= limit then return 1 end
        redis.call('ZADD', KEYS[1], now, ARGV[4])
        redis.call('EXPIRE', KEYS[1], window + 5)
        return 0
        """
        member = f"{now:.6f}:{secrets.token_hex(8)}"
        return bool(await redis_client.eval(script, 1, key, now, window_seconds, limit, member))
    # This fallback is deliberately limited to development/test. Production startup
    # fails closed if shared Redis is not configured and reachable.
    with _rate_limit_lock:
        hits = _rate_limit_hits[key]
        while hits and hits[0] <= now - window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            return True
        hits.append(now)
        return False

@app.middleware("http")
async def request_size_limit(request, call_next):
    # API JSON payloads do not need multi-megabyte bodies. Configure the same or a
    # stricter limit at the reverse proxy; this guard rejects oversized declared bodies.
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            body_size = int(content_length)
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length header."})
        if body_size < 0 or body_size > 1_000_000:
            return JSONResponse(status_code=413, content={"detail": "Request body exceeds the 1 MB limit."})
    return await call_next(request)

@app.middleware("http")
async def rate_limit_high_risk_routes(request, call_next):
    rule = _RATE_LIMIT_RULES.get((request.method.upper(), request.url.path))
    if rule:
        limit, window_seconds = rule
        try:
            exceeded = await _rate_limit_exceeded(request, request.url.path, limit, window_seconds)
        except Exception:
            # Never silently bypass throttling if the shared production store fails.
            if APP_ENV not in {"development", "test"}:
                return JSONResponse(status_code=503, content={"detail": "Request protection is temporarily unavailable."})
            raise
        if exceeded:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please try again later."},
                headers={"Retry-After": str(window_seconds)},
            )
    return await call_next(request)

@app.middleware("http")
async def security_headers(request, call_next):
    if APP_ENV == "production" and request.url.path in {"/demo", "/assets/demo.html"}:
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    if APP_ENV == "production":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'",
        )
    return response


@app.get("/api/health")
def health(): return {"status": "ok", "service": "AlumniConnect API"}

@app.get("/api/health/ready")
async def readiness():
    try:
        with engine.connect() as conn:
            conn.execute(select(1))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Database is unavailable") from exc
    redis_client = getattr(app.state, "rate_limit_redis", None)
    if APP_ENV not in {"development", "test"}:
        if redis_client is None:
            raise HTTPException(status_code=503, detail="Shared rate-limit storage is unavailable")
        try:
            await redis_client.ping()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Shared rate-limit storage is unavailable") from exc
    return {"status": "ready", "database": "connected", "rate_limit_store": "connected" if redis_client else "local-development-only"}

@app.get("/api/health/ai")
def ai_health():
    if _embedding_model is not None:
        state = "loaded"
    elif time.monotonic() < _embedding_retry_after:
        state = "fallback_cooldown"
    else:
        state = "not_loaded"
    return {
        "status": state,
        "model": os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"),
        "semantic_embeddings_active": _embedding_model is not None,
        "fallback_available": True,
    }

@app.get("/api")
def api_root():
    return {"name": "AlumniConnect API", "docs": "/docs" if ENABLE_API_DOCS else None, "health": "/api/health"}

@app.post("/api/auth/register", status_code=201)
def register(payload: RegisterPayload, db: Session = Depends(get_db)):
    email = str(payload.email).lower()
    if db.scalar(select(User).where(User.email == email)): raise HTTPException(status_code=409, detail="An account with this email already exists")
    user = User(email=email, password_hash=_password_hash(payload.password), role=payload.role, is_verified=False)
    db.add(user); db.flush()
    data = payload.model_dump(exclude={"email", "password", "role"})
    profile = Profile(user_id=user.id, **data)
    db.add(profile)
    try: db.commit()
    except IntegrityError: db.rollback(); raise HTTPException(status_code=409, detail="An account with this email already exists")
    db.refresh(user)
    return {"access_token": _token(user), "token_type": "bearer", "user": _public_user(db, user)}

@app.post("/api/auth/login")
def login(payload: LoginPayload, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == str(payload.email).lower()))
    if not user or not _password_ok(payload.password, user.password_hash): raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active: raise HTTPException(status_code=403, detail="This account has been disabled")
    return {"access_token": _token(user), "token_type": "bearer", "user": _public_user(db, user)}

@app.get("/api/auth/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)): return _public_user(db, user)

@app.get("/api/profiles")
def list_profiles(
    q: str = Query(default="", max_length=120),
    department: str = Query(default="", max_length=100),
    skill: str = Query(default="", max_length=80),
    role: str = Query(default="", pattern="^(student|alumni)?$"),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0, le=1_000_000),
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    _ensure_network_eligible(user)
    stmt = select(Profile, User).join(User, User.id == Profile.user_id).where(
        User.is_active.is_(True), User.id != user.id, User.role != "admin",
        or_(User.role != "alumni", User.is_verified.is_(True)),
    )
    if q: stmt = stmt.where(or_(Profile.full_name.ilike(f"%{q}%"), Profile.company.ilike(f"%{q}%"), Profile.job_title.ilike(f"%{q}%"), Profile.interests.ilike(f"%{q}%")))
    if department: stmt = stmt.where(Profile.department.ilike(f"%{department}%"))
    if skill: stmt = stmt.where(func.cast(Profile.skills, String).ilike(f"%{skill}%"))
    if role in {"student", "alumni"}: stmt = stmt.where(User.role == role)
    rows = db.execute(stmt.order_by(Profile.full_name).offset(offset).limit(limit)).all()
    return [{**_profile_dict(p), "role": u.role, "is_verified": u.is_verified, "email": u.email if u.role == "alumni" and u.is_verified else None} for p, u in rows]

@app.get("/api/profiles/{profile_user_id}")
def get_profile(profile_user_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    target = db.get(User, profile_user_id); profile = db.get(Profile, profile_user_id)
    if not target or not profile or not target.is_active:
        raise HTTPException(status_code=404, detail="Profile not found")
    if target.role == "admin" and target.id != user.id and user.role != "admin":
        raise HTTPException(status_code=404, detail="Profile not found")
    if target.role == "alumni" and not target.is_verified and target.id != user.id and user.role != "admin":
        raise HTTPException(status_code=404, detail="Profile not found")
    data = _profile_dict(profile); data.update({"role": target.role, "is_verified": target.is_verified})
    if target.id == user.id or (target.role == "alumni" and target.is_verified): data["email"] = target.email
    return data

@app.put("/api/profiles/me")
def update_profile(payload: ProfilePayload, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = db.get(Profile, user.id)
    for key, value in payload.model_dump().items(): setattr(p, key, value)
    p.embedding = _embed(_profile_text(p))
    db.commit(); db.refresh(p)
    return {**_profile_dict(p), "embedding_status": "ready" if p.embedding else "pending_model_or_fallback"}

@app.get("/api/recommendations")
def recommendations(limit: int = Query(10, ge=1, le=50), user: User = Depends(current_user), db: Session = Depends(get_db)):
    _ensure_network_eligible(user)
    mine = db.get(Profile, user.id)
    if not mine: raise HTTPException(status_code=404, detail="Complete your profile first")
    if not mine.embedding: mine.embedding = _embed(_profile_text(mine))
    rows = db.execute(
        select(Profile, User).join(User, User.id == Profile.user_id).where(
            User.id != user.id, User.is_active.is_(True),
            User.role == "alumni", User.is_verified.is_(True),
        ).order_by(Profile.updated_at.desc()).limit(500)
    ).all()
    results = []
    for candidate, account in rows:
        if not candidate.embedding: candidate.embedding = _embed(_profile_text(candidate))
        if mine.embedding and candidate.embedding: score = max(0.0, min(1.0, _cosine(mine.embedding, candidate.embedding)))
        else: score = _fallback_similarity(_profile_text(mine), _profile_text(candidate))
        common = sorted(set(s.lower() for s in (mine.skills or [])) & set(s.lower() for s in (candidate.skills or [])))
        reasons = []
        if common: reasons.append("Shared skills: " + ", ".join(common[:4]))
        if mine.department and mine.department.lower() == candidate.department.lower(): reasons.append("Same academic department")
        if mine.career_goal and (mine.career_goal.lower() in _profile_text(candidate).lower() or candidate.job_title.lower() in mine.career_goal.lower()): reasons.append("Career goal aligns with their experience")
        if not reasons: reasons.append("Your interests and professional profile have semantic similarities")
        results.append({**_profile_dict(candidate), "role": account.role, "is_verified": account.is_verified,
                        "match_score": round(score * 100), "reasons": reasons, "embedding_used": bool(mine.embedding and candidate.embedding)})
    db.commit()
    return sorted(results, key=lambda item: (item["match_score"], item["is_verified"]), reverse=True)[:limit]

@app.post("/api/connections", status_code=201)
def request_connection(payload: ConnectionPayload, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _ensure_network_eligible(user)
    target = db.get(User, payload.recipient_id)
    if not target or not target.is_active or target.id == user.id:
        raise HTTPException(status_code=400, detail="Invalid connection recipient")
    if target.role == "alumni" and not _eligible_alumnus(target):
        raise HTTPException(status_code=404, detail="Alumni profile not found")
    existing = db.scalar(select(Connection).where(or_((Connection.requester_id == user.id) & (Connection.recipient_id == target.id), (Connection.requester_id == target.id) & (Connection.recipient_id == user.id))))
    if existing: raise HTTPException(status_code=409, detail=f"A connection request already exists ({existing.status})")
    item = Connection(requester_id=user.id, recipient_id=target.id); db.add(item); _notify(db, target.id, f"{user.email} sent you a connection request."); db.commit(); db.refresh(item)
    return {"id": item.id, "requester_id": item.requester_id, "recipient_id": item.recipient_id, "status": item.status}

@app.get("/api/connections")
def list_connections(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Connection).where(or_(Connection.requester_id == user.id, Connection.recipient_id == user.id)).order_by(Connection.created_at.desc())).all()
    return [{"id": c.id, "requester_id": c.requester_id, "recipient_id": c.recipient_id, "status": c.status, "incoming": c.recipient_id == user.id, "participant": _network_participant(db, c.requester_id if c.recipient_id == user.id else c.recipient_id), "created_at": c.created_at} for c in rows]

@app.patch("/api/connections/{connection_id}")
def respond_connection(connection_id: int, payload: StatusPayload, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _ensure_network_eligible(user)
    item = db.get(Connection, connection_id)
    if not item: raise HTTPException(status_code=404, detail="Connection request not found")
    if item.recipient_id != user.id: raise HTTPException(status_code=403, detail="Only the recipient can respond")
    if item.status != "pending": raise HTTPException(status_code=409, detail="This request has already been processed")
    requester = db.get(User, item.requester_id)
    if requester and requester.role == "alumni" and not _eligible_alumnus(requester):
        raise HTTPException(status_code=409, detail="The requesting alumni account is no longer verified")
    item.status = payload.status; _notify(db, item.requester_id, f"Your connection request was {payload.status}."); db.commit()
    return {"id": item.id, "status": item.status}

@app.post("/api/mentorship", status_code=201)
def request_mentorship(payload: MentorshipPayload, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.role != "student": raise HTTPException(status_code=403, detail="Only student accounts can request mentorship")
    target = db.get(User, payload.alumni_id)
    if not target or not _eligible_alumnus(target):
        raise HTTPException(status_code=404, detail="Verified alumni profile not found")
    item = MentorshipRequest(student_id=user.id, alumni_id=target.id, message=payload.message); db.add(item); _notify(db, target.id, f"New mentorship request from {user.email}."); db.commit(); db.refresh(item)
    return {"id": item.id, "student_id": item.student_id, "alumni_id": item.alumni_id, "message": item.message, "status": item.status}

@app.get("/api/mentorship")
def list_mentorship(user: User = Depends(current_user), db: Session = Depends(get_db)):
    stmt = select(MentorshipRequest).where(or_(MentorshipRequest.student_id == user.id, MentorshipRequest.alumni_id == user.id)).order_by(MentorshipRequest.created_at.desc())
    return [{"id": r.id, "student_id": r.student_id, "alumni_id": r.alumni_id, "message": r.message, "status": r.status, "incoming": r.alumni_id == user.id, "participant": _network_participant(db, r.student_id if r.alumni_id == user.id else r.alumni_id), "created_at": r.created_at} for r in db.scalars(stmt).all()]

@app.patch("/api/mentorship/{request_id}")
def respond_mentorship(request_id: int, payload: StatusPayload, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _ensure_network_eligible(user)
    item = db.get(MentorshipRequest, request_id)
    if not item: raise HTTPException(status_code=404, detail="Mentorship request not found")
    if item.alumni_id != user.id: raise HTTPException(status_code=403, detail="Only the requested alumni can respond")
    if item.status != "pending": raise HTTPException(status_code=409, detail="This request has already been processed")
    item.status = payload.status; _notify(db, item.student_id, f"Your mentorship request was {payload.status}."); db.commit()
    return {"id": item.id, "status": item.status}

@app.get("/api/posts")
def list_posts(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Post, Profile).join(Profile, Profile.user_id == Post.author_id).order_by(Post.created_at.desc()).offset(offset).limit(limit)).all()
    result = []
    for post, profile in rows:
        result.append({
            "id": post.id, "author_id": post.author_id, "author": profile.full_name,
            "title": post.title, "body": post.body, "category": post.category,
            "created_at": post.created_at,
            "liked": db.scalar(select(PostReaction.id).where(PostReaction.post_id == post.id, PostReaction.user_id == user.id, PostReaction.kind == "liked").limit(1)) is not None,
            "bookmarked": db.scalar(select(PostReaction.id).where(PostReaction.post_id == post.id, PostReaction.user_id == user.id, PostReaction.kind == "bookmarked").limit(1)) is not None,
            "likes_count": db.scalar(select(func.count()).select_from(PostReaction).where(PostReaction.post_id == post.id, PostReaction.kind == "liked")) or 0,
            "comments_count": db.scalar(select(func.count()).select_from(PostComment).where(PostComment.post_id == post.id)) or 0,
        })
    return result

@app.post("/api/posts", status_code=201)
def create_post(payload: PostPayload, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = Post(author_id=user.id, **payload.model_dump()); db.add(item); db.commit(); db.refresh(item)
    return {"id": item.id, "author_id": item.author_id, "title": item.title, "body": item.body, "category": item.category, "created_at": item.created_at}

@app.put("/api/posts/{post_id}/reactions/{kind}")
def set_post_reaction(post_id: int, kind: Literal["liked", "bookmarked"], payload: PostReactionPayload, user: User = Depends(current_user), db: Session = Depends(get_db)):
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    reaction = db.scalar(select(PostReaction).where(PostReaction.post_id == post_id, PostReaction.user_id == user.id, PostReaction.kind == kind))
    if payload.enabled and reaction is None:
        db.add(PostReaction(post_id=post_id, user_id=user.id, kind=kind))
    elif not payload.enabled and reaction is not None:
        db.delete(reaction)
    db.commit()
    count = db.scalar(select(func.count()).select_from(PostReaction).where(PostReaction.post_id == post_id, PostReaction.kind == kind)) or 0
    return {"post_id": post_id, "kind": kind, "enabled": payload.enabled, "count": count}

@app.get("/api/posts/{post_id}/comments")
def list_post_comments(post_id: int, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not db.get(Post, post_id):
        raise HTTPException(status_code=404, detail="Post not found")
    rows = db.execute(select(PostComment, Profile, User).join(Profile, Profile.user_id == PostComment.author_id).join(User, User.id == PostComment.author_id).where(PostComment.post_id == post_id).order_by(PostComment.created_at.asc()).offset(offset).limit(limit)).all()
    return [{"id": comment.id, "post_id": post_id, "author_id": comment.author_id, "author": profile.full_name, "role": account.role, "body": comment.body, "created_at": comment.created_at} for comment, profile, account in rows]

@app.post("/api/posts/{post_id}/comments", status_code=201)
def create_post_comment(post_id: int, payload: PostCommentPayload, user: User = Depends(current_user), db: Session = Depends(get_db)):
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    comment = PostComment(post_id=post_id, author_id=user.id, body=payload.body)
    db.add(comment)
    if post.author_id != user.id:
        _notify(db, post.author_id, f"{user.email} commented on your post.")
    db.commit()
    db.refresh(comment)
    profile = db.get(Profile, user.id)
    return {"id": comment.id, "post_id": post_id, "author_id": user.id, "author": profile.full_name, "role": user.role, "body": comment.body, "created_at": comment.created_at}

@app.post("/api/messages", status_code=201)
def send_message(payload: MessagePayload, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _ensure_network_eligible(user)
    target = db.get(User, payload.recipient_id)
    if not target or not target.is_active or target.id == user.id:
        raise HTTPException(status_code=400, detail="Invalid message recipient")
    if target.role == "alumni" and not _eligible_alumnus(target):
        raise HTTPException(status_code=404, detail="Alumni profile not found")
    connected = db.scalar(select(Connection).where(Connection.status == "accepted", or_((Connection.requester_id == user.id) & (Connection.recipient_id == target.id), (Connection.requester_id == target.id) & (Connection.recipient_id == user.id))))
    if not connected: raise HTTPException(status_code=403, detail="Messaging is available after a connection is accepted")
    item = Message(sender_id=user.id, recipient_id=target.id, body=payload.body); db.add(item); _notify(db, target.id, f"New message from {user.email}."); db.commit(); db.refresh(item)
    return {"id": item.id, "sender_id": item.sender_id, "recipient_id": item.recipient_id, "body": item.body, "created_at": item.created_at}

@app.get("/api/messages/{other_user_id}")
def get_messages(other_user_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _ensure_network_eligible(user)
    if other_user_id == user.id:
        raise HTTPException(status_code=400, detail="Choose another user to view a conversation")
    connected = db.scalar(select(Connection).where(Connection.status == "accepted", or_((Connection.requester_id == user.id) & (Connection.recipient_id == other_user_id), (Connection.requester_id == other_user_id) & (Connection.recipient_id == user.id))))
    if not connected:
        raise HTTPException(status_code=403, detail="Messages are available only between accepted connections")
    other_user = db.get(User, other_user_id)
    if not other_user or not other_user.is_active:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if other_user.role == "alumni" and not _eligible_alumnus(other_user):
        raise HTTPException(status_code=404, detail="Conversation not found")
    rows = db.scalars(select(Message).where(or_((Message.sender_id == user.id) & (Message.recipient_id == other_user_id), (Message.sender_id == other_user_id) & (Message.recipient_id == user.id))).order_by(Message.created_at.asc())).all()
    for message in rows:
        if message.recipient_id == user.id and message.read_at is None: message.read_at = datetime.now(timezone.utc)
    db.commit()
    return [{"id": m.id, "sender_id": m.sender_id, "recipient_id": m.recipient_id, "body": m.body, "read": m.read_at is not None, "created_at": m.created_at} for m in rows]

@app.get("/api/notifications")
def notifications(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Notification).where(Notification.user_id == user.id).order_by(Notification.created_at.desc()).limit(100)).all()
    return [{"id": n.id, "message": n.message, "read": n.read_at is not None, "created_at": n.created_at} for n in rows]

@app.patch("/api/notifications/{notification_id}/read")
def read_notification(notification_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = db.get(Notification, notification_id)
    if not item or item.user_id != user.id: raise HTTPException(status_code=404, detail="Notification not found")
    item.read_at = datetime.now(timezone.utc); db.commit(); return {"id": item.id, "read": True}

@app.get("/api/admin/users")
def admin_users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.execute(select(User, Profile).join(Profile, Profile.user_id == User.id).order_by(User.created_at.desc())).all()
    return [{"id": u.id, "email": u.email, "role": u.role, "is_active": u.is_active, "is_verified": u.is_verified, "profile": _profile_dict(p)} for u, p in rows]

@app.patch("/api/admin/users/{user_id}/verify")
def verify_user(user_id: int, verified: bool = Query(True), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if not target or target.role != "alumni": raise HTTPException(status_code=404, detail="Alumni account not found")
    target.is_verified = verified; _notify(db, target.id, "Your alumni profile was verified." if verified else "Your alumni verification was removed."); db.commit()
    return {"id": target.id, "is_verified": target.is_verified}

@app.patch("/api/admin/users/{user_id}/active")
def set_user_active(user_id: int, active: bool = Query(...), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if not target or target.id == admin.id: raise HTTPException(status_code=404, detail="User not found")
    target.is_active = active; db.commit(); return {"id": target.id, "is_active": target.is_active}

# Legacy demo-state endpoints keep the supplied single-page UI compatible.
@app.get("/api/state")
def get_state():
    if not ENABLE_LEGACY_DEMO_STATE:
        raise HTTPException(status_code=404, detail="Legacy demo-state API is disabled")
    with SessionLocal() as db:
        row = db.get(AppState, 1)
        if row is None: row = AppState(id=1, state=seed_state()); db.add(row); db.commit(); db.refresh(row)
        return row.state

@app.put("/api/state")
def save_state(payload: StatePayload):
    if not ENABLE_LEGACY_DEMO_STATE:
        raise HTTPException(status_code=404, detail="Legacy demo-state API is disabled")
    with SessionLocal() as db:
        row = db.get(AppState, 1); data = payload.model_dump()
        if row is None: row = AppState(id=1, state=data); db.add(row)
        else: row.state = data
        db.commit(); return {"saved": True, "updated_at": row.updated_at.isoformat() if row.updated_at else None}

app.mount("/assets", StaticFiles(directory=FRONTEND_DIR), name="assets")
@app.get("/", include_in_schema=False)
def home(): return FileResponse(FRONTEND_DIR / "index.html")

@app.get("/demo", include_in_schema=False)
def legacy_demo():
    if APP_ENV == "production":
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(FRONTEND_DIR / "demo.html")
