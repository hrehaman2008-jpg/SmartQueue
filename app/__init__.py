"""Smart Queue and Digital Token System — application factory."""

import os
from datetime import datetime
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

# Initialize extensions
db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login"  # type: ignore
login_manager.login_message_category = "info"


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

    # Ensure instance folder exists
    try:
        os.makedirs(app.instance_path)
    except OSError:
        pass

    # Initialize extensions with app
    db.init_app(app)
    login_manager.init_app(app)
    
    @app.context_processor
    def inject_now():
        return {"now": datetime.now}

    # Register blueprints
    from app.routes import bp as main_bp
    app.register_blueprint(main_bp)

    # Create database tables for the first time
    with app.app_context():
        db.create_all()
        # Atomic demo seeding (opt-in via ENABLE_DEMO_SEEDING=1)
        if os.getenv("ENABLE_DEMO_SEEDING") == "1":
            from .models import User, Doctor, Receptionist
            from .config import BaseConfig
            doc_pw = os.getenv("DEMO_DOCTOR_PASSWORD")
            rec_pw = os.getenv("DEMO_RECEPTIONIST_PASSWORD")
            if doc_pw and rec_pw:
                try:
                    for username, password, role, profile_cls, profile_data in [
                        (BaseConfig.DEMO_DOCTOR_USERNAME, doc_pw, "doctor", Doctor, {"full_name": "Demo Doctor"}),
                        (BaseConfig.DEMO_RECEPTIONIST_USERNAME, rec_pw, "receptionist", Receptionist, {"full_name": "Demo Receptionist"}),
                    ]:
                        user = User.query.filter_by(username=username).first()
                        if not user:
                            user = User(username=username, role=role)
                            user.set_password(password)
                            db.session.add(user)
                            db.session.flush() # Ensure ID is assigned

                        # Verify role and check if profile is missing
                        if user.role == role and not getattr(user, role):
                            db.session.add(profile_cls(user_id=user.id, **profile_data))
                    db.session.commit()
                except Exception as e:
                    db.session.rollback()
                    app.logger.error(f"Demo seeding failed: {e}")

    return app