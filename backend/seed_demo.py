"""Seed the local SQLite database with synthetic matching showcase accounts.

This command is explicit and idempotent. It never runs during app startup and
refuses production/staging environments and non-SQLite database URLs.
"""

import os


def seed_demo_data() -> None:
    raw_app_env = os.getenv("APP_ENV", "development").strip().lower()
    app_env = {"dev": "development", "local": "development"}.get(raw_app_env, raw_app_env)
    database_url = os.getenv("DATABASE_URL", "")
    if app_env not in {"development", "test"}:
        raise SystemExit("Demo data can only be seeded in development or test environments.")
    if database_url and not database_url.startswith("sqlite:"):
        raise SystemExit("Demo data can only be seeded into a SQLite database.")

    from sqlalchemy import select

    from backend.app.main import Base, Profile, SessionLocal, User, _password_hash, engine

    student_password = "DemoStudent!2026"
    alumni_password = "DemoAlumni!2026"
    accounts = [
        {
            "email": "demo.student@example.com",
            "password": student_password,
            "role": "student",
            "is_verified": False,
            "full_name": "Demo Student",
            "department": "Computer Science & Engineering",
            "graduation_year": 2027,
            "company": "",
            "job_title": "Computer Science Student",
            "skills": ["Python", "SQL", "Machine Learning"],
            "interests": "Applied machine learning, natural language processing, recommendation systems, and building useful AI products.",
            "career_goal": "Machine Learning Engineer",
            "bio": "Synthetic showcase profile for demonstrating student to alumni matching.",
        },
        {
            "email": "demo.alumni.maya@example.com",
            "password": alumni_password,
            "role": "alumni",
            "is_verified": True,
            "full_name": "Maya Iyer",
            "department": "Computer Science & Engineering",
            "graduation_year": 2022,
            "company": "Northstar AI",
            "job_title": "Machine Learning Engineer",
            "skills": ["Python", "Machine Learning", "NLP", "PyTorch", "Recommendation Systems"],
            "interests": "Applied machine learning, natural language processing, recommendation systems, and responsible AI.",
            "career_goal": "",
            "bio": "Synthetic alumni profile. Enjoys mentoring students on ML projects and early career growth.",
        },
        {
            "email": "demo.alumni.arjun@example.com",
            "password": alumni_password,
            "role": "alumni",
            "is_verified": True,
            "full_name": "Arjun Menon",
            "department": "Computer Science & Engineering",
            "graduation_year": 2020,
            "company": "DataBridge Analytics",
            "job_title": "Data Scientist",
            "skills": ["Python", "SQL", "Machine Learning", "Statistics", "Power BI"],
            "interests": "Data science, predictive analytics, machine learning, and turning data into product decisions.",
            "career_goal": "",
            "bio": "Synthetic alumni profile. Can share advice on analytics portfolios and interview preparation.",
        },
        {
            "email": "demo.alumni.nisha@example.com",
            "password": alumni_password,
            "role": "alumni",
            "is_verified": True,
            "full_name": "Nisha Rao",
            "department": "Information Technology",
            "graduation_year": 2021,
            "company": "CloudScale Systems",
            "job_title": "Cloud Platform Engineer",
            "skills": ["Python", "AWS", "Docker", "Kubernetes", "Distributed Systems"],
            "interests": "Cloud platforms, Python services, scalable software, and infrastructure automation.",
            "career_goal": "",
            "bio": "Synthetic alumni profile. Mentors students interested in cloud and production engineering.",
        },
        {
            "email": "demo.alumni.kabir@example.com",
            "password": alumni_password,
            "role": "alumni",
            "is_verified": True,
            "full_name": "Kabir Shah",
            "department": "Computer Science & Engineering",
            "graduation_year": 2019,
            "company": "Civic Product Studio",
            "job_title": "Technical Product Manager",
            "skills": ["Product Strategy", "SQL", "User Research", "Experimentation", "Analytics"],
            "interests": "Building useful digital products, product analytics, experimentation, and user research.",
            "career_goal": "",
            "bio": "Synthetic alumni profile. Shares guidance on product roles and translating user needs into software.",
        },
    ]

    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        for account_data in accounts:
            user = db.scalar(select(User).where(User.email == account_data["email"]))
            if user is None:
                user = User(
                    email=account_data["email"],
                    password_hash=_password_hash(account_data["password"]),
                    role=account_data["role"],
                    is_active=True,
                    is_verified=account_data["is_verified"],
                )
                db.add(user)
                db.flush()
            else:
                user.password_hash = _password_hash(account_data["password"])
                user.role = account_data["role"]
                user.is_active = True
                user.is_verified = account_data["is_verified"]

            profile_data = {
                key: value
                for key, value in account_data.items()
                if key not in {"email", "password", "role", "is_verified"}
            }
            profile_data["embedding"] = None
            profile = db.get(Profile, user.id)
            if profile is None:
                db.add(Profile(user_id=user.id, **profile_data))
            else:
                for key, value in profile_data.items():
                    setattr(profile, key, value)

        db.commit()

    print("Seeded 1 synthetic student and 4 verified synthetic alumni.")
    print("Student login: demo.student@example.com / DemoStudent!2026")
    print("Alumni logins: demo.alumni.<name>@example.com / DemoAlumni!2026")
    print("All demo data is fabricated and stored only in the selected local SQLite database.")


if __name__ == "__main__":
    seed_demo_data()
