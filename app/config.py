
"""Configuration classes for the Smart Queue application."""

import os
from pathlib import Path


class BaseConfig:
    """Common configuration shared by all environments."""

    # Published development fallback — never a real secret. Production
    # refuses to boot with it (see the startup guard in create_app).
    DEV_SECRET_KEY = "dev-secret-change-me"

    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", DEV_SECRET_KEY)

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

    # IANA timezone the clinic's booking form is expressed in.
    # Submitted naive datetimes are interpreted in this zone and stored
    # as UTC. Default preserves the historical "input is UTC" behaviour.
    CLINIC_TIMEZONE = os.getenv("CLINIC_TIMEZONE", "UTC")

    # Session and security
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 8

    # CSRF
    WTF_CSRF_TIME_LIMIT = 3600

    # Startup-safety flags consumed by create_app:
    # - REQUIRE_UNIQUE_SECRET: refuse to boot without a real FLASK_SECRET_KEY
    # - DEMO_SEEDING_ALLOW_DEFAULT_PASSWORDS: whether demo seeding may fall
    #   back to the published development demo passwords when the matching
    #   DEMO_*_PASSWORD env var is not set (production keeps this False).
    REQUIRE_UNIQUE_SECRET = False
    DEMO_SEEDING_ALLOW_DEFAULT_PASSWORDS = True

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

    # Production fails closed:
    # - a real FLASK_SECRET_KEY is mandatory at startup, and
    # - demo accounts are only ever seeded from explicitly configured
    #   DEMO_*_PASSWORD env vars, never from the published defaults.
    REQUIRE_UNIQUE_SECRET = True
    DEMO_SEEDING_ALLOW_DEFAULT_PASSWORDS = False


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
