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

    return app