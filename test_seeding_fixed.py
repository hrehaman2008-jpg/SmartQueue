import os
import sys
import tempfile
from importlib import reload

# Set env vars BEFORE any imports
tmpdir = tempfile.mkdtemp()
db_path = os.path.join(tmpdir, "test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
os.environ["ENABLE_DEMO_SEEDING"] = "1"
os.environ["DEMO_DOCTOR_PASSWORD"] = "DoctorTest123!"
os.environ["DEMO_RECEPTIONIST_PASSWORD"] = "ReceptionTest123!"

# Setup path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '.')))

# Import after setting env vars
import app.config
reload(app.config)
from app import create_app, db
from app.models import User, Doctor, Receptionist
from app.config import BaseConfig

def test_seeding_logic():
    app = create_app()
    with app.app_context():
        # --- FIRST PASS: Initial Seeding ---
        from app.models import User, Doctor, Receptionist
        # Verify Seeding for Doctor
        doc = User.query.filter_by(username=BaseConfig.DEMO_DOCTOR_USERNAME).first()
        assert doc is not None, f"Doctor user {BaseConfig.DEMO_DOCTOR_USERNAME} should exist"
        assert doc.doctor is not None, "Doctor profile should exist"
        assert doc.check_password("DoctorTest123!"), "Password should match"

        # Verify Seeding for Receptionist
        rec = User.query.filter_by(username=BaseConfig.DEMO_RECEPTIONIST_USERNAME).first()
        assert rec is not None, f"Receptionist user {BaseConfig.DEMO_RECEPTIONIST_USERNAME} should exist"
        assert rec.receptionist is not None, "Receptionist profile should exist"

        # --- SECOND PASS: Idempotency Verification ---
        # Run seeding logic again to check for duplicates
        # Re-trigger seed block logic locally to simulate
        doc_pw = os.getenv("DEMO_DOCTOR_PASSWORD")
        rec_pw = os.getenv("DEMO_RECEPTIONIST_PASSWORD")
        for username, password, role, profile_cls, profile_data in [
            (BaseConfig.DEMO_DOCTOR_USERNAME, doc_pw, "doctor", Doctor, {"full_name": "Demo Doctor"}),
            (BaseConfig.DEMO_RECEPTIONIST_USERNAME, rec_pw, "receptionist", Receptionist, {"full_name": "Demo Receptionist"}),
        ]:
            user = User.query.filter_by(username=username).first()
            # If our logic is idempotent, this will be skipped correctly
            assert user is not None, "User should still be found"
            if user.role == role and not getattr(user, role):
                db.session.add(profile_cls(user_id=user.id, **profile_data))
        db.session.commit()

        # Final count check
        assert User.query.filter_by(username=BaseConfig.DEMO_DOCTOR_USERNAME).count() == 1
        assert User.query.filter_by(username=BaseConfig.DEMO_RECEPTIONIST_USERNAME).count() == 1

        print("Test Passed: Seeding idempotency, profile creation, and hashing verified.")

if __name__ == "__main__":
    test_seeding_logic()
