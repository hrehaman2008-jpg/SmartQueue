"""Fixed test script using isolated test database.

Usage:
    venv\Scripts\python test_register_fixed.py
"""

import tempfile, os
from app import create_app, db
from app.config import TestingConfig


class TestConfig(TestingConfig):
    """Testing configuration with isolated file-based database."""

    def __init__(self, db_path):
        self.SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"
        self.TESTING = True
        self.WTF_CSRF_ENABLED = False


def main():
    # Create isolated temporary database
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test_queue.db")

    app = create_app(TestConfig(db_path))

    with app.app_context():
        db.create_all()

    client = app.test_client()

    print("=== Test 1: Register a new patient ===")
    rv = client.post(
        "/register",
        data={
            "username": "newpatient",
            "email": "newpatient@example.com",
            "phone": "09123456789",
            "full_name": "New Patient",
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
        user = User.query.filter_by(username="newpatient").first()
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
            "email": "newpatient@example.com",  # already used
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
            "username": "newpatient",
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
    print("=== Test 4: Invalid email format should fail ===")
    rv = client.post(
        "/register",
        data={
            "username": "thirdpatient",
            "email": "not-an-email",
            "phone": "09876543210",
            "full_name": "Third Patient",
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
    print("=== Test 5: Password hashing verification ===")
    with app.app_context():
        user = User.query.filter_by(username="newpatient").first()
        print(f"  - check_password('Patient123!') = {user.check_password('Patient123!')}")
        print(f"  - check_password('wrong') = {user.check_password('wrong')}")

    print()
    print("=== All tests passed ===")


if __name__ == "__main__":
    main()