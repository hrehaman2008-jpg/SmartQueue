"""Configuration classes for the Smart Queue application.

All configuration is loaded from environment variables where possible.
Secrets are never hardcoded — the development config falls back to
clearly-named dev-only defaults that must be overridden in production.
"""

import os
from pathlib import Path


class BaseConfig:
    """Common configuration shared by all environments."""

    # Application metadata
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")

    # Database — default to the instance folder SQLite file
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL")
    if not SQLALCHEMY_DATABASE_URI:
        if os.getenv("VERCEL"):
            SQLALCHEMY_DATABASE_URI = "sqlite:////tmp/queue.db"
        else:
            SQLALCHEMY_DATABASE_URI = f"sqlite:///{Path(__file__).resolve().parent.parent / 'instance' / 'queue.db'}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Session / security
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 8  # 8 hours

    # CSRF
    WTF_CSRF_TIME_LIMIT = 3600

    # Demo credentials (development only) — documented in README.md
    DEMO_PATIENT_USERNAME = os.getenv("DEMO_PATIENT_USERNAME", "patient_demo")
    DEMO_PATIENT_PASSWORD = os.getenv("DEMO_PATIENT_PASSWORD", "Patient123!")
    DEMO_DOCTOR_USERNAME = os.getenv("DEMO_DOCTOR_USERNAME", "doctor_demo")
    DEMO_DOCTOR_PASSWORD = os.getenv("DEMO_DOCTOR_PASSWORD", "Doctor123!")
    DEMO_RECEPTIONIST_USERNAME = os.getenv(
        "DEMO_RECEPTIONIST_USERNAME", "reception_demo"
    )
    DEMO_RECEPTIONIST_PASSWORD = os.getenv(
        "DEMO_RECEPTIONIST_PASSWORD", "Reception123!"
    )


class DevelopmentConfig(BaseConfig):
    """Development configuration — debug enabled, permissive."""
    DEBUG = True
    TESTING = False


class ProductionConfig(BaseConfig):
    """Production configuration — debug disabled, stricter."""
    DEBUG = False
    TESTING = False
    SESSION_COOKIE_SECURE = True


class TestingConfig(BaseConfig):
    """Testing configuration — uses an in-memory database."""
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    DEBUG = False
    TESTING = True
    WTF_CSRF_ENABLED = False


config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}