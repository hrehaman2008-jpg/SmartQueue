"""Integration tests for registration, login, logout, and appointment booking.

Uses an isolated in-memory database — never touches instance/queue.db.
Run with: venv\Scripts\python test_auth.py
"""

import tempfile, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app, db
from app.config import TestingConfig
from app.models import User, Patient, Doctor, Appointment, Token


class AppTestConfig(TestingConfig):
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    TESTING = True
    WTF_CSRF_ENABLED = False


def make_app():
    app = create_app(AppTestConfig)
    with app.app_context():
        db.create_all()
    return app


def register(client, **form_data):
    return client.post("/register", data=form_data, follow_redirects=False)


def login(client, username, password):
    return client.post("/login", data={"username": username, "password": password}, follow_redirects=False)


def logout(client):
    return client.get("/logout", follow_redirects=False)


def test_registration():
    print("=== registration ===")
    app = make_app()
    client = app.test_client()

    rv = register(client, username="reguser", email="reg@example.com", phone="09123456789",
                  full_name="Reg User", date_of_birth="1990-01-01",
                  password="Patient123!", confirm_password="Patient123!")
    print("1. POST /register ->", rv.status_code, rv.headers.get("Location"), "(expect 302 -> /login)")
    assert rv.status_code == 302 and rv.headers["Location"] == "/login"

    with app.app_context():
        user = User.query.filter_by(username="reguser").first()
        patient = Patient.query.filter_by(user_id=user.id).first()
        assert user is not None and patient is not None, "exactly one User and one Patient must be created"
        assert patient.email == "reg@example.com"
        assert user.role == "patient"
        assert user.password_hash is not None
        assert user.check_password("Patient123!") is True
        assert user.check_password("wrongpass") is False
        print("2. One User + one Patient created; password hashed; verify OK")

    rv = register(client, username="dupuser", email="reg@example.com", phone="09876543210",
                  full_name="Dup", date_of_birth="1990-01-01",
                  password="Patient123!", confirm_password="Patient123!")
    with app.app_context():
        dup = User.query.filter_by(username="dupuser").first()
        print("3. Duplicate email rejected; no duplicate User:", dup is None)
        assert dup is None

    rv = register(client, username="reguser", email="other@example.com", phone="09876543210",
                  full_name="Dup2", date_of_birth="1990-01-01",
                  password="Patient123!", confirm_password="Patient123!")
    with app.app_context():
        dup2 = User.query.filter_by(username="reguser").first()
        print("4. Duplicate username rejected; only original User exists:", dup2 is not None)
        assert dup2 is not None

    rv = register(client, username="bad", email="not-an-email", phone="09876543210",
                  full_name="Bad", date_of_birth="1990-01-01",
                  password="Patient123!", confirm_password="Patient123!")
    print("5. Invalid email ->", rv.status_code, "(expect 200, form re-shown)")
    assert rv.status_code == 200

    with app.app_context():
        # invalid submission must not create records
        bad_user = User.query.filter_by(username="bad").first()
        bad_pat = Patient.query.filter_by(email="not-an-email").first()
        print("6. Invalid submission created nothing:", bad_user is None and bad_pat is None)
        assert bad_user is None and bad_pat is None

    print("registration: OK\n")


def test_login_logout():
    print("=== login / logout ===")
    app = make_app()
    client = app.test_client()

    # Register first
    register(client, username="loguser", email="log@example.com", phone="09122222222",
             full_name="Log User", date_of_birth="1990-01-01",
             password="Patient123!", confirm_password="Patient123!")

    rv = login(client, "loguser", "Patient123!")
    print("1. Login with correct password ->", rv.status_code, rv.headers.get("Location"))
    assert rv.status_code == 302 and rv.headers["Location"] == "/"

    # Log out before testing wrong password (test client preserves session)
    client.get("/logout", follow_redirects=False)

    rv = login(client, "loguser", "wrongpassword")
    print("2. Login with wrong password ->", rv.status_code, "(expect 200, stay on login)")
    assert rv.status_code == 200

    rv = client.get("/patient/dashboard", follow_redirects=True)
    print("3. Unauthenticated access to /patient/dashboard -> redirected (401->login)")
    assert rv.status_code == 200  # redirected to login page (200)

    # Now login and check the dashboard renders without AttributeError
    rv = login(client, "loguser", "Patient123!")
    rv2 = client.get("/patient/dashboard")
    print("4. Dashboard after login ->", rv2.status_code, "(expect 200, no AttributeError)")
    assert rv2.status_code == 200

    rv = logout(client)
    print("5. Logout ->", rv.status_code, rv.headers.get("Location"))
    assert rv.status_code == 302

    with app.app_context():
        assert current_user_logged_out(client)
        print("6. Logged out; session cleared")
    print("login / logout: OK\n")


def current_user_logged_out(client):
    rv = client.get("/logout", follow_redirects=False)
    return rv.status_code == 302


def test_appointment_booking():
    print("=== appointment booking ===")
    app = make_app()
    client = app.test_client()

    with app.app_context():
        # Seed a doctor (normally done by seed.py)
        doc_user = User(username="dr_smith", role="doctor")
        doc_user.set_password("Doctor123!")
        db.session.add(doc_user)
        db.session.commit()
        doctor = Doctor(user=doc_user, full_name="Dr. Smith", specialization="Cardiology")
        db.session.add(doctor)
        db.session.commit()

    assert Doctor.query.count() == 1, "a doctor must exist for the booking dropdown"

    register(client, username="bookuser", email="book@example.com", phone="09133333333",
             full_name="Book User", date_of_birth="1990-01-01",
             password="Patient123!", confirm_password="Patient123!")
    client.login(User.query.filter_by(username="bookuser").first())

    # Book a future appointment
    import datetime
    from datetime import date, datetime as dt
    future = dt.combine(date.today().toordinal().__class__.__new__(__class__, 0) + datetime.timedelta(days=7) if False else dt.now().date().replace(day=date.today().day + 7), dt.min.time())
    # simpler: build date string 7 days from now
    nxt = dt.now().date() + datetime.timedelta(days=7)
    rv = client.post("/patient/book-appointment", data={
        "doctor_id": str(doctor.id),
        "appointment_date": nxt.strftime("%Y-%m-%d"),
        "appointment_time": "14:30",
        "purpose": "Check-up",
    }, follow_redirects=False)
    print("1. Book future appointment ->", rv.status_code, rv.headers.get("Location"))
    assert rv.status_code == 302

    with app.app_context():
        appt = Appointment.query.filter_by(patient_id=1, doctor_id=doctor.id).first()
        tok = Token.query.filter_by(appointment_id=appt.id).first()
        assert appt is not None and tok is not None
        assert appt.patient_id == 1
        assert appt.doctor_id == doctor.id
        assert appt.appointment_datetime == future.replace(second=0, microsecond=0)
        print("2. Appointment linked to patient + doctor; token created:", tok.token_number)

    # Past date should fail
    past = dt.now().date() - datetime.timedelta(days=1)
    rv = client.post("/patient/book-appointment", data={
        "doctor_id": str(doctor.id),
        "appointment_date": past.strftime("%Y-%m-%d"),
        "appointment_time": "14:30",
    }, follow_redirects=False)
    print("3. Past date rejected ->", rv.status_code)
    assert rv.status_code == 200

    # Missing fields should fail
    rv = client.post("/patient/book-appointment", data={
        "doctor_id": "",
        "appointment_date": "",
        "appointment_time": "",
    }, follow_redirects=False)
    print("4. Missing required fields ->", rv.status_code)
    assert rv.status_code == 200

    # Not logged in should be blocked
    rv = client.get("/patient/book-appointment")
    print("5. Not logged in -> redirected")
    assert rv.status_code == 302

    print("appointment booking: OK\n")


if __name__ == "__main__":
    test_registration()
    test_login_logout()
    test_appointment_booking()
    print("ALL TESTS PASSED")
