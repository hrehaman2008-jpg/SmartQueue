
"""Configuration classes for the Smart Queue application."""

import os
from pathlib import Path


class BaseConfig:
    """Common configuration shared by all environments."""

    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")

    # Database configuration
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL")

    if SQLALCHEMY_DATABASE_URI:
        if SQLALCHEMY_DATABASE_URI.startswith("postgres://"):
            SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace(
                "postgres://", "postgresql+psycopg://", 1
            )
        elif SQLALCHEMY_DATABASE_URI.startswith("postgresql://"):
            SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace(
                "postgresql://", "postgresql+psycopg://", 1
            )
    else:
        if os.getenv("VERCEL"):
            SQLALCHEMY_DATABASE_URI = "sqlite:////tmp/queue.db"
        else:
            SQLALCHEMY_DATABASE_URI = (
                f"sqlite:///{Path(__file__).resolve().parent.parent / 'instance' / 'queue.db'}"
            )

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Session and security
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 8

    # CSRF
    WTF_CSRF_TIME_LIMIT = 3600

    # Demo credentials
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
    """Development configuration."""
    DEBUG = True
    TESTING = False


class ProductionConfig(BaseConfig):
    """Production configuration."""
    DEBUG = False
    TESTING = False
    SESSION_COOKIE_SECURE = True


class TestingConfig(BaseConfig):
    """Testing configuration."""
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    DEBUG = False
    TESTING = True
    WTF_CSRF_ENABLED = False


config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}
