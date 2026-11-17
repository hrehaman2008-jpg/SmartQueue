"""Smart Queue and Digital Token System — application factory."""

import os
from datetime import datetime
from flask import Flask, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect, CSRFError
from sqlalchemy.exc import IntegrityError

from .config import BaseConfig

# Initialize extensions
db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "main.login"  # single "main" blueprint hosts the login route
login_manager.login_message_category = "info"
csrf = CSRFProtect()


def create_app(config_object=None):
    """Application factory pattern."""
    app = Flask(__name__, instance_relative_config=True)

    # Load configuration
    if config_object is None:
        # Use environment variable or default
        config_name = os.getenv("FLASK_CONFIG", "development")
        app.config.from_object(f"app.config.{config_name.capitalize()}Config")
    else:
        app.config.from_object(config_object)

    # Startup safety: production must never sign sessions with the
    # published development fallback secret. The error message is
    # deliberately explicit and never echoes any configured value.
    if app.config.get("REQUIRE_UNIQUE_SECRET"):
        secret = app.config.get("SECRET_KEY")
        if not secret or secret == BaseConfig.DEV_SECRET_KEY:
            raise RuntimeError(
                "Production startup blocked: FLASK_SECRET_KEY is missing or "
                "still the built-in development default. Set FLASK_SECRET_KEY "
                "to a unique random value before starting in production "
                "(generate one with: python -c \"import secrets; "
                "print(secrets.token_hex(32))\")."
            )

    # Ensure instance folder exists
    try:
        os.makedirs(app.instance_path)
    except OSError:
        pass

    # Initialize extensions with app
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)  # enforces CSRF tokens on all state-changing POSTs

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        """Render CSRF rejections as a styled, actionable page.

        Without this, Flask-WTF's global protection answers a stale or
        missing CSRF token with a bare, unstyled Werkzeug 400 page and no
        way forward. That is especially confusing on /login: a page left
        open longer than WTF_CSRF_TIME_LIMIT (1h) submits an expired
        token, so the user sees a dead-end error even with the correct
        password. The rejection itself is unchanged — still HTTP 400 and
        still fully enforced — only the presentation improves.
        """
        return (
            render_template("error/400.html", reason=error.description),
            400,
        )

    @app.context_processor
    def inject_now():
        return {"now": datetime.now}

    # Register blueprints
    from app.routes import bp as main_bp
    app.register_blueprint(main_bp)

    # Create database tables for the first time.
    # Guarded: a transient DB outage at serverless cold start must not
    # crash the whole app — routes surface their own DB errors instead.
    with app.app_context():
        db_ready = True
        try:
            db.create_all()
        except Exception as exc:  # noqa: BLE001 - app must still boot
            db_ready = False
            app.logger.error("Database initialization failed: %s", exc)

        # Atomic demo seeding (opt-in via ENABLE_DEMO_SEEDING=1)
        if db_ready and os.getenv("ENABLE_DEMO_SEEDING") == "1":
            seed_demo_accounts(app)

    return app


def seed_demo_accounts(app):
    """Create the demo doctor, receptionist and patient accounts if missing.

    Idempotent and safe under concurrent serverless cold starts:
    - each account is committed independently;
    - an existing username is reused, never overwritten or deleted;
    - unique-constraint races with a parallel invocation are ignored;
    - a user that exists with a different role is left untouched;
    - an account is only seeded when its password comes from an explicit
      DEMO_*_PASSWORD env var, or (non-production only) from the
      documented development defaults — production never falls back to
      the published default passwords.
    """
    from .models import User, Patient, Doctor, Receptionist

    accounts = [
        ("DEMO_DOCTOR_PASSWORD", BaseConfig.DEMO_DOCTOR_USERNAME, "doctor", Doctor, {"full_name": "Demo Doctor"}),
        ("DEMO_RECEPTIONIST_PASSWORD", BaseConfig.DEMO_RECEPTIONIST_USERNAME, "receptionist", Receptionist, {"full_name": "Demo Receptionist"}),
        ("DEMO_PATIENT_PASSWORD", BaseConfig.DEMO_PATIENT_USERNAME, "patient", Patient, {"full_name": "Demo Patient"}),
    ]
    allow_default_passwords = app.config.get("DEMO_SEEDING_ALLOW_DEFAULT_PASSWORDS", True)

    for password_env, username, role, profile_cls, profile_data in accounts:
        # Read the env var at call time so operators can rotate demo
        # credentials without a code change.
        password = os.getenv(password_env)
        if not password:
            if allow_default_passwords:
                # Development convenience only: documented defaults.
                password = getattr(BaseConfig, password_env)
            else:
                app.logger.warning(
                    "Demo seeding: %s is not set; skipping %r. This "
                    "environment never seeds accounts with default passwords.",
                    password_env, username,
                )
                continue
        try:
            user = User.query.filter_by(username=username).first()
            if user is None:
                user = User(username=username, role=role)
                user.set_password(password)
                db.session.add(user)
                db.session.flush()

            if user.role != role:
                app.logger.warning(
                    "Demo seeding: user %r already exists with role %r (expected %r); leaving it untouched.",
                    username, user.role, role,
                )
                db.session.rollback()
                continue

            if getattr(user, role) is None:
                db.session.add(profile_cls(user_id=user.id, **profile_data))

            db.session.commit()
        except IntegrityError:
            # A parallel cold start created the same user/profile first;
            # the account exists either way, so there is nothing to do.
            db.session.rollback()
            app.logger.info("Demo seeding: %r already created concurrently.", username)
        except Exception as exc:  # noqa: BLE001 - seeding must never break boot
            db.session.rollback()
            app.logger.error("Demo seeding failed for %r: %s", username, exc)
