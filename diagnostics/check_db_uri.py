from app import create_app, db

app = create_app()
with app.app_context():
    print(f"Engine URL: {db.engine.url}")
