"""Regression tests for the P1 + P2 fixes.

Covers:
- logged-out access redirects to the real login endpoint (no BuildError)
- receptionist dashboard / walk-in / queue pages render with context
- walk-in token creation populates every required field (token_number!)
- QR delivery is generated in memory (nothing written to disk)
- demo seeding is idempotent, non-destructive, and role-safe
- the app still boots when the database is unreachable
- patient booking + doctor call -> complete workflows
- CSRF-protected POST-only call-next

Uses an isolated in-memory database — never touches instance/queue.db
or any production database. Run with:

    venv\\Scripts\\python test_p1_fixes.py
"""

import os
import re
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app, db, seed_demo_accounts
from app.config import TestingConfig
from app.models import User, Patient, Doctor, Receptionist, Appointment, Token

PASS = 0
FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}  {detail}")


def make_app():
    """Fresh app with its own in-memory database."""
    return create_app(TestingConfig)


def make_user(app, username, role, password="password1", **profile):
    with app.app_context():
        user = User.query.filter_by(username=username).first()
        if user is None:
            user = User(username=username, role=role)
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
            cls = {"patient": Patient, "doctor": Doctor, "receptionist": Receptionist}[role]
            defaults = {"full_name": username.title()}
            defaults.update(profile)
            db.session.add(cls(user_id=user.id, **defaults))
            db.session.commit()
        return user.id


def login(app, username, password):
    client = app.test_client()
    resp = client.post("/login", data={"username": username, "password": password},
                       follow_redirects=False)
    return client, resp


# ──────────────────────────────────────────────────────────────────
# 1. Logged-out access to protected routes -> redirect to real login
# ──────────────────────────────────────────────────────────────────
def test_logged_out_redirects():
    print("\n[1] Logged-out access redirects to /login (no BuildError)")
    app = make_app()
    client = app.test_client()
    protected = [
        "/patient/dashboard", "/patient/book-appointment", "/patient/history",
        "/patient/profile", "/receptionist/dashboard", "/receptionist/walkin",
        "/receptionist/queue", "/doctor/dashboard", "/doctor/appointments",
        "/logout",
    ]
    for path in protected:
        resp = client.get(path, follow_redirects=False)
        loc = resp.headers.get("Location", "")
        check(f"GET {path} redirects to login",
              resp.status_code == 302 and loc.startswith("/login"),
              f"got {resp.status_code} Location={loc!r}")


# ──────────────────────────────────────────────────────────────────
# 2. Receptionist pages render with proper context
# ──────────────────────────────────────────────────────────────────
def test_receptionist_pages():
    print("\n[2] Receptionist pages render")
    app = make_app()
    make_user(app, "rec1", "receptionist")
    client, resp = login(app, "rec1", "password1")
    check("receptionist login", resp.status_code == 302, f"got {resp.status_code}")

    resp = client.get("/receptionist/dashboard")
    body = resp.get_data(as_text=True)
    check("dashboard renders", resp.status_code == 200, f"got {resp.status_code}")
    check("dashboard shows waiting count", "Patients Currently Waiting" in body)

    resp = client.get("/receptionist/walkin")
    check("walk-in form renders", resp.status_code == 200, f"got {resp.status_code}")

    resp = client.get("/receptionist/queue")
    check("queue board renders", resp.status_code == 200, f"got {resp.status_code}")
    check("queue board title", "General Queue (walk-ins)" in resp.get_data(as_text=True))


# ──────────────────────────────────────────────────────────────────
# 3. Walk-in token creation fills every required field
# ──────────────────────────────────────────────────────────────────
def test_walkin_token_creation():
    print("\n[3] Walk-in token creation")
    app = make_app()
    make_user(app, "rec1", "receptionist")
    client, _ = login(app, "rec1", "password1")

    resp = client.post("/receptionist/walkin",
                       data={"full_name": "Jane Walk", "phone": "+966 55 123", "email": ""},
                       follow_redirects=False)
    check("first walk-in POST redirects (no 500)",
          resp.status_code == 302, f"got {resp.status_code}")

    with app.app_context():
        token = Token.query.order_by(Token.id.desc()).first()
        check("token exists", token is not None)
        check("token_number populated",
              bool(token and re.fullmatch(r"T-[0-9A-Z]{4,6}", token.token_number or "")),
              f"got {token.token_number if token else None!r}")
        check("token is active with position 1",
              bool(token and token.status == "active" and token.queue_position == 1))
        check("patient profile created for walk-in",
              bool(token and token.patient and token.patient.phone == "+966 55 123"))
        first_patient_id = token.patient_id

    # Second walk-in with same phone must reuse the patient (no duplicates)
    resp = client.post("/receptionist/walkin",
                       data={"full_name": "Jane Walk", "phone": "+966 55 123", "email": ""},
                       follow_redirects=False)
    check("second walk-in POST redirects", resp.status_code == 302, f"got {resp.status_code}")
    with app.app_context():
        check("exactly one patient for that phone",
              Patient.query.filter_by(phone="+966 55 123").count() == 1)
        check("second token issued",
              Token.query.filter_by(patient_id=first_patient_id).count() == 2)
        check("general queue positions 1..2",
              [t.queue_position for t in Token.query.filter_by(doctor_id=None).order_by(Token.queue_position)] == [1, 2])
        # walk-in account must not carry an empty/guessable password hash
        walkin_user = User.query.filter_by(username="walkin_96655123").first()
        check("walk-in username sanitized", walkin_user is not None,
              "expected username walkin_96655123")
        check("walk-in password is a real hash",
              bool(walkin_user and walkin_user.password_hash
                   and not walkin_user.check_password("")))


# ──────────────────────────────────────────────────────────────────
# 4. QR generation is in-memory (Vercel read-only FS compatible)
# ──────────────────────────────────────────────────────────────────
def test_qr_in_memory():
    print("\n[4] QR generation")
    app = make_app()
    make_user(app, "rec1", "receptionist")
    client, _ = login(app, "rec1", "password1")

    qr_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "app", "static", "qrcode")
    files_before = sorted(os.listdir(qr_dir)) if os.path.isdir(qr_dir) else []

    client.post("/receptionist/walkin",
                data={"full_name": "QR Patient", "phone": "777", "email": ""})
    with app.app_context():
        token = Token.query.order_by(Token.id.desc()).first()
        number = token.token_number
        check("qr_code_url points at dynamic route",
              token.qr_code_url == f"/qr/{number}", f"got {token.qr_code_url!r}")

    # Security fix (M1): the QR route now requires authentication.
    anon = app.test_client()
    resp = anon.get(f"/qr/{number}")
    loc = resp.headers.get("Location", "")
    check("GET /qr/<n> anonymous -> redirect to login",
          resp.status_code == 302 and loc.startswith("/login"),
          f"got {resp.status_code} Location={loc!r}")

    # Logged-in receptionist (authorized staff) still gets the image
    resp = client.get(f"/qr/{number}")
    body = resp.get_data()
    check("GET /qr/<n> returns 200 for authorized staff",
          resp.status_code == 200, f"got {resp.status_code}")
    check("content-type image/png",
          resp.content_type.startswith("image/png"), resp.content_type)
    check("response is a real PNG", body[:8] == b"\x89PNG\r\n\x1a\n", body[:8].hex())
    check("unknown token -> 404",
          client.get("/qr/T-9999").status_code == 404)

    files_after = sorted(os.listdir(qr_dir)) if os.path.isdir(qr_dir) else []
    check("no files written to disk", files_before == files_after,
          f"{files_before} -> {files_after}")


# ──────────────────────────────────────────────────────────────────
# 5. Demo seeding: idempotent, non-destructive, role-safe
# ──────────────────────────────────────────────────────────────────
def test_seeding_idempotent():
    print("\n[5] Demo seeding")
    app = make_app()
    with app.app_context():
        seed_demo_accounts(app)
        n_users = User.query.count()
        check("3 demo users created", n_users == 3, f"got {n_users}")
        check("all profiles exist",
              Patient.query.count() == 1
              and Doctor.query.count() == 1
              and Receptionist.query.count() == 1)

        # Run twice more: must not duplicate or raise
        seed_demo_accounts(app)
        seed_demo_accounts(app)
        check("repeated seeding is idempotent", User.query.count() == 3,
              f"got {User.query.count()}")
        check("no duplicate profiles after re-seed",
              Patient.query.count() == 1 and Doctor.query.count() == 1
              and Receptionist.query.count() == 1)

        # Passwords actually work for seeded accounts
        doc = User.query.filter_by(username="doctor_demo").first()
        check("seeded doctor password matches configured value",
              bool(doc and doc.check_password(doc and "Doctor123!")))
        pat = User.query.filter_by(username="patient_demo").first()
        check("patient_demo account exists (was never seeded before)",
              pat is not None and pat.role == "patient")

    # Existing user with the SAME username but a DIFFERENT role: untouched
    app2 = make_app()
    with app2.app_context():
        clash = User(username="doctor_demo", role="patient")
        clash.set_password("whatever123")
        db.session.add(clash)
        db.session.commit()
        original_id, original_role = clash.id, clash.role
        seed_demo_accounts(app2)
        kept = db.session.get(User, original_id)
        check("conflicting-role user not deleted or modified",
              kept is not None and kept.role == original_role
              and kept.password_hash == clash.password_hash)
        check("conflicting seed did not create a second doctor_demo",
              User.query.filter_by(username="doctor_demo").count() == 1)

    # Pre-existing healthy account (right role, profile present): untouched
    app3 = make_app()
    with app3.app_context():
        doc = User(username="doctor_demo", role="doctor")
        doc.set_password("OriginalPass1!")
        db.session.add(doc)
        db.session.flush()
        db.session.add(Doctor(user_id=doc.id, full_name="Real Doctor"))
        db.session.commit()
        original_hash = doc.password_hash
        seed_demo_accounts(app3)
        refreshed = User.query.filter_by(username="doctor_demo").first()
        check("existing demo account not overwritten",
              refreshed.password_hash == original_hash)
        check("no duplicate doctor profile created", Doctor.query.count() == 1)


# ──────────────────────────────────────────────────────────────────
# 6. App boots even when the database is unreachable
# ──────────────────────────────────────────────────────────────────
def test_boots_on_db_failure():
    print("\n[6] App boot resilience (create_all guarded)")

    class BrokenConfig(TestingConfig):
        # Nonexistent directory -> connection/create_all fails
        SQLALCHEMY_DATABASE_URI = "sqlite:////nonexistent-smartqueue-dir/app.db"

    try:
        app = create_app(BrokenConfig)
    except Exception as exc:  # noqa: BLE001
        check("create_app survives DB failure", False, f"raised {exc!r}")
        return
    check("create_app survives DB failure", True)
    resp = app.test_client().get("/")
    check("public pages still serve when DB is down",
          resp.status_code == 200, f"got {resp.status_code}")


# ──────────────────────────────────────────────────────────────────
# 7. Patient registration + booking workflow
# ──────────────────────────────────────────────────────────────────
def test_patient_booking_flow():
    print("\n[7] Patient booking flow")
    app = make_app()
    make_user(app, "doc1", "doctor", specialization="General Medicine")
    with app.app_context():
        doc_id = Doctor.query.filter_by(full_name="Doc1").first().id

    client = app.test_client()
    resp = client.post("/register", data={
        "username": "alice", "email": "alice@example.com", "phone": "123",
        "full_name": "Alice", "password": "password1",
        "confirm_password": "password1",
    }, follow_redirects=False)
    check("registration succeeds", resp.status_code == 302 and resp.location == "/login",
          f"got {resp.status_code} {resp.location}")

    resp = client.post("/register", data={
        "username": "alice", "email": "other@example.com", "phone": "9",
        "full_name": "X", "password": "password1", "confirm_password": "password1",
    })
    check("duplicate username rejected",
          "already taken" in resp.get_data(as_text=True))

    client.post("/login", data={"username": "alice", "password": "password1"})
    resp = client.get("/patient/dashboard")
    check("patient dashboard renders", resp.status_code == 200, f"got {resp.status_code}")

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    resp = client.post("/patient/book-appointment", data={
        "doctor_id": str(doc_id), "appointment_date": tomorrow,
        "appointment_time": "10:00", "purpose": "checkup",
    }, follow_redirects=False)
    check("booking succeeds", resp.status_code == 302, f"got {resp.status_code}")
    with app.app_context():
        check("appointment stored", Appointment.query.count() == 1)
        token = Token.query.filter_by(doctor_id=doc_id).first()
        check("token created for appointment",
              bool(token and token.appointment_id is not None))
        check("booking did not write QR files",
              token.qr_code_path is None, f"got {token.qr_code_path!r}")

    resp = client.get("/patient/dashboard")
    body = resp.get_data(as_text=True)
    check("dashboard shows the token", "T-" in body)

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    resp = client.post("/patient/book-appointment", data={
        "doctor_id": str(doc_id), "appointment_date": yesterday,
        "appointment_time": "10:00", "purpose": "x",
    })
    check("past-date booking rejected",
          "today or later" in resp.get_data(as_text=True)
          or "must be in the future" in resp.get_data(as_text=True))

    # clinic timezone conversion: config default = UTC (historical behaviour)
    with app.app_context():
        from app.utils import utc_from_clinic_time
        from datetime import datetime as dt
        check("UTC clinic tz is identity",
              utc_from_clinic_time(dt(2026, 10, 21, 10, 0), "UTC") == dt(2026, 10, 21, 10, 0))
        check("unknown tz falls back safely",
              utc_from_clinic_time(dt(2026, 10, 21, 10, 0), "Not/AZone") == dt(2026, 10, 21, 10, 0))
        shifted = utc_from_clinic_time(dt(2026, 10, 21, 10, 0), "Asia/Riyadh")
        check("IANA tz converts to UTC", shifted == dt(2026, 10, 21, 7, 0), f"got {shifted}")


# ──────────────────────────────────────────────────────────────────
# 8. Doctor call -> complete workflow (POST-only, CSRF-protected)
# ──────────────────────────────────────────────────────────────────
def test_doctor_call_complete_flow():
    print("\n[8] Doctor call -> complete flow")
    app = make_app()
    make_user(app, "doc1", "doctor", specialization="Cardiology")
    make_user(app, "pat1", "patient")
    with app.app_context():
        doc_id = Doctor.query.first().id
        pat_id = Patient.query.first().id

    # Two tokens straight into the doctor's queue
    with app.app_context():
        from app.utils import generate_token_number, recalc_queue_positions
        t1 = Token(patient_id=pat_id, doctor_id=doc_id,
                   token_number=generate_token_number(), status="active")
        db.session.add(t1)
        db.session.flush()
        recalc_queue_positions(doc_id)
        db.session.commit()
    with app.app_context():
        from app.utils import generate_token_number, recalc_queue_positions
        t2 = Token(patient_id=pat_id, doctor_id=doc_id,
                   token_number=generate_token_number(), status="active")
        db.session.add(t2)
        db.session.flush()
        recalc_queue_positions(doc_id)
        db.session.commit()
        check("queue positions 1..2",
              [t.queue_position for t in Token.query.filter_by(doctor_id=doc_id).order_by(Token.queue_position)] == [1, 2])

    doctor, _ = login(app, "doc1", "password1")

    resp = doctor.get("/doctor/call-next", follow_redirects=False)
    check("GET call-next is rejected (405)", resp.status_code == 405,
          f"got {resp.status_code}")

    resp = doctor.post("/doctor/call-next", follow_redirects=False)
    check("POST call-next works", resp.status_code == 302, f"got {resp.status_code}")
    with app.app_context():
        called = Token.query.filter_by(status="called").first()
        check("top token is now called", called is not None)
        called_id = called.id
        called_number = called.token_number

    resp = doctor.get("/doctor/dashboard")
    body = resp.get_data(as_text=True)
    check("dashboard renders called token as Currently Serving",
          resp.status_code == 200 and called_number in body,
          f"got {resp.status_code}")
    check("remaining token still in waiting queue",
          "Waiting Queue" in body and body.count("T-") >= 2)

    resp = doctor.post(f"/doctor/complete/{called_id}", follow_redirects=False)
    check("complete works", resp.status_code == 302, f"got {resp.status_code}")
    with app.app_context():
        t = db.session.get(Token, called_id)
        check("token completed with timestamp",
              t.status == "completed" and t.completed_at is not None)
        remaining = Token.query.filter_by(doctor_id=doc_id, status="active").first()
        check("remaining token repositioned to 1",
              bool(remaining and remaining.queue_position == 1),
              f"got {remaining.queue_position if remaining else None}")

    resp = doctor.get("/doctor/dashboard")
    check("serving slot cleared after completion",
          "No active consultation." in resp.get_data(as_text=True))


# ──────────────────────────────────────────────────────────────────
# 9. General walk-in queue handoff to doctors (Option A)
# ──────────────────────────────────────────────────────────────────
def test_general_queue_handoff():
    print("\n[9] General walk-in queue handoff")
    app = make_app()
    make_user(app, "rec1", "receptionist")
    make_user(app, "doc1", "doctor", full_name="Doc One")
    make_user(app, "doc2", "doctor", full_name="Doc Two")
    pat_id = make_user(app, "pat1", "patient")

    # A receptionist registers a walk-in (exactly as the UI does)
    rec, _ = login(app, "rec1", "password1")
    resp = rec.post("/receptionist/walkin",
                    data={"full_name": "Joel Miller", "phone": "555-0142", "email": ""},
                    follow_redirects=False)
    check("walk-in registration succeeds", resp.status_code == 302,
          f"got {resp.status_code}")
    with app.app_context():
        g1 = Token.query.filter_by(doctor_id=None).order_by(Token.generated_at, Token.id).first()
        g1_id, g1_no = g1.id, g1.token_number
        check("walk-in token is active with doctor_id=None",
              g1.status == "active" and g1.doctor_id is None)

    # The doctor dashboard now shows the unassigned walk-in
    d1, _ = login(app, "doc1", "password1")
    body = d1.get("/doctor/dashboard").get_data(as_text=True)
    check("doctor dashboard shows the walk-in token",
          g1_no in body, f"{g1_no} missing from dashboard")
    check("dashboard no longer shows the empty state",
          "Queue is empty" not in body)

    # Call Next marks it called and shows it as Currently Serving
    resp = d1.post("/doctor/call-next", follow_redirects=False)
    check("call-next succeeds", resp.status_code == 302, f"got {resp.status_code}")
    with app.app_context():
        g1 = db.session.get(Token, g1_id)
        check("walk-in is now 'called', doctor_id still None",
              g1.status == "called" and g1.doctor_id is None)
        first_called_at = g1.called_at
    body = d1.get("/doctor/dashboard").get_data(as_text=True)
    check("Currently Serving shows the walk-in token",
          g1_no in body and "Currently Serving" in body)

    # A second doctor cannot call the same active walk-in
    d2, _ = login(app, "doc2", "password1")
    resp = d2.post("/doctor/call-next", follow_redirects=False)
    flash_body = d2.get("/doctor/dashboard").get_data(as_text=True)
    with app.app_context():
        g1 = db.session.get(Token, g1_id)
        check("second doctor's call-next does not re-call the token",
              g1.status == "called" and g1.called_at == first_called_at,
              f"status={g1.status} called_at changed={g1.called_at != first_called_at}")
    check("second doctor sees 'No patients waiting'",
          "No patients waiting" in flash_body)
    # Compare-and-set race: a stale read's guarded UPDATE must match 0 rows
    with app.app_context():
        raced = Token.query.filter(Token.id == g1_id, Token.status == "active").update(
            {"status": "called"}, synchronize_session=False)
        db.session.rollback()
        check("stale CAS on same token matches 0 rows", raced == 0, f"matched {raced}")

    # Completing the general walk-in recalculates remaining general positions
    resp = d1.post(f"/doctor/complete/{g1_id}", follow_redirects=False)
    check("doctor completes the general walk-in", resp.status_code == 302)
    rec2, _ = login(app, "rec1", "password1")
    rec2.post("/receptionist/walkin", data={"full_name": "Ana Ruiz", "phone": "555-0222"})
    rec2.post("/receptionist/walkin", data={"full_name": "Sam Cole", "phone": "555-0333"})
    with app.app_context():
        g1 = db.session.get(Token, g1_id)
        check("completed walk-in keeps doctor_id=None",
              g1.status == "completed" and g1.doctor_id is None)
        active_general = (Token.query.filter_by(doctor_id=None, status="active")
                          .order_by(Token.generated_at, Token.id).all())
        check("two active general tokens",
              len(active_general) == 2, f"got {len(active_general)}")
        check("general positions gapless 1..2",
              [t.queue_position for t in active_general] == [1, 2],
              f"got {[t.queue_position for t in active_general]}")
        g3_id = active_general[0].id
        g4_id = active_general[1].id

    # Skipping a general walk-in recalculates without gaps
    resp = d1.post(f"/doctor/skip/{g3_id}", follow_redirects=False)
    check("doctor skips a general walk-in", resp.status_code == 302)
    with app.app_context():
        g3 = db.session.get(Token, g3_id)
        g4 = db.session.get(Token, g4_id)
        check("skipped token status set, doctor_id still None",
              g3.status == "skipped" and g3.doctor_id is None)
        check("remaining general token renumbered to 1 (no gap)",
              g4.queue_position == 1, f"got {g4.queue_position}")

    # Receptionist queue view reflects the resulting state
    board = rec2.get("/receptionist/queue").get_data(as_text=True)
    check("receptionist board hides completed/skipped tokens",
          g1_no not in board)
    with app.app_context():
        g4_no = db.session.get(Token, g4_id).token_number
    check("receptionist board shows remaining walk-in at position 1",
          g4_no in board)

    # Doctor-assigned appointments continue to work alongside the shared queue
    with app.app_context():
        from app.utils import generate_token_number, recalc_queue_positions
        doc = Doctor.query.filter_by(full_name="Doc One").first()
        t_assigned = Token(patient_id=pat_id, doctor_id=doc.id,
                           token_number=generate_token_number(), status="active")
        db.session.add(t_assigned)
        db.session.flush()
        recalc_queue_positions(doc.id)
        db.session.commit()
        a1_id = t_assigned.id
        a1_no = t_assigned.token_number
        doc_id = doc.id  # plain int: instances detach when the context exits
    body = d1.get("/doctor/dashboard").get_data(as_text=True)
    check("assigned token and walk-in both visible",
          a1_no in body and g4_no in body)
    resp = d1.post(f"/doctor/complete/{a1_id}", follow_redirects=False)
    check("assigned-token complete still works", resp.status_code == 302)
    with app.app_context():
        a1 = db.session.get(Token, a1_id)
        check("assigned token completed with doctor_id preserved",
              a1.status == "completed" and a1.doctor_id == doc_id)
        # general queue untouched by an assigned-token action
        g4 = db.session.get(Token, g4_id)
        check("general queue unaffected by assigned-token complete",
              g4.status == "active" and g4.queue_position == 1)

    # Cross-doctor isolation for ASSIGNED tokens still holds (404)
    resp = d2.post(f"/doctor/complete/{a1_id}", follow_redirects=False)
    check("other doctor's assigned token still 404",
          resp.status_code == 404, f"got {resp.status_code}")


# ──────────────────────────────────────────────────────────────────
def main():
    tests = [
        test_logged_out_redirects,
        test_receptionist_pages,
        test_walkin_token_creation,
        test_qr_in_memory,
        test_seeding_idempotent,
        test_boots_on_db_failure,
        test_patient_booking_flow,
        test_doctor_call_complete_flow,
        test_general_queue_handoff,
    ]
    for test in tests:
        test()
    print(f"\n{'=' * 60}\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
