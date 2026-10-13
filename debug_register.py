"""Minimal debug: single registration, print raw response + form errors."""

import tempfile, os
from app import create_app, db
from app.forms import RegistrationForm

tmpdir = tempfile.mkdtemp()
db_path = os.path.join(tmpdir, "dbg.db")
app = create_app()
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
app.config["TESTING"] = True
app.config["WTF_CSRF_ENABLED"] = False

with app.app_context():
    db.create_all()

client = app.test_client()

print("=== Single registration POST ===")
rv = client.post(
    "/register",
    data={
        "username": "dbguser",
        "email": "dbg@example.com",
        "phone": "09123456789",
        "full_name": "Dbg User",
        "date_of_birth": "1990-01-01",
        "password": "Password123!",
        "confirm_password": "Password123!",
    },
    follow_redirects=False,
)
print(f"status={rv.status_code} location={rv.headers.get('Location')}")
print(f"body[:300]={rv.data[:300]!r}")

with app.app_context():
    from app.models import User, Patient
    u = User.query.filter_by(username="dbguser").first()
    p = Patient.query.filter_by(user_id=u.id).first() if u else None
    print(f"user={u.username if u else None} role={u.role if u else None}")
    print(f"patient={p.full_name if p else None} email={p.email if p else None}")

print("=== GET /register (form) ===")
rv = client.get("/register")
print(f"status={rv.status_code}")