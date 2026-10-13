"""Test script for patient registration.

Usage:
    venv\Scripts\python test_register.py
"""

import sqlite3
import tempfile
import os

# Use a temporary in-memory-free DB so we don't touch the real queue.db
from app import create_app, db


def main():
    # Create app with a fresh SQLite file
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test_queue.db")
    app = create_app()
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False  # disable CSRF for test client

    with app.app_context():
        db.create_all()

    client = app.test_client()

    print("=== Test 1: Register a new patient ===")
    rv = client.post(
        "/register",
        data={
            "username": "testpatient",
            "email": "testpatient@example.com",
            "phone": "09123456789",
            "full_name": "Test Patient",
            "date_of_birth": "1990-01-01",
            "password": "Patient123!",
            "confirm_password": "Patient123!",
        },
        follow_redirects=False,
    )
    print(f"Response status: {rv.status_code}")
    print(f"Redirect location: {rv.headers.get('Location')}")

    from app.models import User, Patient

    with app.app_context():
        user = User.query.filter_by(username="testpatient").first()
        patient = Patient.query.filter_by(user_id=user.id).first() if user else None

    print(f"User exists: {user is not None}")
    if user:
        print(f"  - username: {user.username}")
        print(f"  - email: {getattr(user, 'email', 'N/A (user has no email column)')}")
        print(f"  - role: {user.role}")
        print(f"  - password_hash set: {user.password_hash is not None}")
        print(f"  - password check works: {user.check_password('Patient123!')}")
        print(f"  - patient profile exists: {patient is not None}")
        if patient:
            print(f"  - patient.full_name: {patient.full_name}")
            print(f"  - patient.phone: {patient.phone}")
            print(f"  - patient.email: {patient.email}")
            print(f"  - patient.date_of_birth: {patient.date_of_birth}")

    print()
    print("=== Test 2: Duplicate email should fail ===")
    rv = client.post(
        "/register",
        data={
            "username": "anotherpatient",
            "email": "testpatient@example.com",  # already used
            "phone": "09876543210",
            "full_name": "Another Patient",
            "date_of_birth": "1995-05-05",
            "password": "Patient123!",
            "confirm_password": "Patient123!",
        },
        follow_redirects=True,
    )
    print(f"Response status: {rv.status_code}")
    if rv.status_code == 200:
        print("Form was re-displayed (validation error) - PASS")
    elif rv.status_code in [301, 302, 303]:
        print(f"Redirected to: {rv.headers.get('Location')} - FAIL (should not redirect)")

    print()
    print("=== Test 3: Duplicate username should fail ===")
    rv = client.post(
        "/register",
        data={
            "username": "testpatient",
            "email": "different@example.com",
            "phone": "09876543210",
            "full_name": "Duplicate User",
            "date_of_birth": "1995-05-05",
            "password": "Patient123!",
            "confirm_password": "Patient123!",
        },
        follow_redirects=True,
    )
    print(f"Response status: {rv.status_code}")
    if rv.status_code == 200:
        print("Form was re-displayed (validation error) - PASS")
    elif rv.status_code in [301, 302, 303]:
        print(f"Redirected to: {rv.headers.get('Location')} - FAIL (should not redirect)")

    print()
    print("=== Test 4: Password hashing is preserved ===")
    with app.app_context():
        user = User.query.filter_by(username="testpatient").first()
        print(f"  - check_password('Patient123!') = {user.check_password('Patient123!')}")
        print(f"  - check_password('wrong') = {user.check_password('wrong')}")

    print()
    print("=== All tests completed ===")


if __name__ == "__main__":
    main()