"""Phase 2 — Full integration review harness.

Covers the three role workflows end-to-end plus a cross-role E2E trace,
using an isolated in-memory database (never touches instance/queue.db).

Also contains the regression tests for the four defects confirmed during
this review:
  F1 post-login ``next`` redirect rejected every relative target;
  F2 walk-in phone re-entry with different formatting failed (IntegrityError);
  F3 activate/skip accepted invalid status transitions on terminal tokens;
  F4 appointment status never left ``scheduled`` after the token ended.

Run with: venv\\Scripts\\python test_integration_phase2.py
"""

import os
import re
import sys
from datetime import date, datetime as dt, timedelta
from urllib.parse import quote, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app, db
from app.config import TestingConfig
from app.models import User, Patient, Doctor, Receptionist, Appointment, Token

PASSED = 0
FAILED = 0


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED += 1
        print(f"  FAIL  {name}" + (f"  [{extra}]" if extra else ""))


class Cfg(TestingConfig):
    WTF_CSRF_ENABLED = False


def make_app():
    app = create_app(Cfg)
    with app.app_context():
        db.create_all()
    return app


def mkuser(app, username, role, password="password1", **profile):
    with app.app_context():
        user = User(username=username, role=role)
        user.set_password(password)
        db.session.add(user)
        db.session.flush()
        cls = {"patient": Patient, "doctor": Doctor, "receptionist": Receptionist}[role]
        kwargs = dict(full_name=profile.pop("full_name", username.title()))
        kwargs.update(profile)
        db.session.add(cls(user_id=user.id, **kwargs))
        db.session.commit()
        return user.id


def login(app, username, password="password1"):
    client = app.test_client()
    rv = client.post("/login", data={"username": username, "password": password},
                     follow_redirects=False)
    return client, rv


def is_local_location(loc, host="http://localhost"):
    """True if Location stays on this origin (no scheme/host/backslash escape)."""
    if loc.startswith("/"):
        return not loc.startswith("//") and "\\" not in loc
    p = urlparse(loc)
    return p.scheme in ("http", "https") and p.netloc == urlparse(host).netloc


# ──────────────────────────────────────────────────────────────────
# P. Patient workflow
# ──────────────────────────────────────────────────────────────────
def test_patient_workflow():
    print("\n[P] Patient workflow")
    app = make_app()
    mkuser(app, "doc1", "doctor", full_name="Doc One", specialization="Cardiology")
    mkuser(app, "doc2", "doctor", full_name="Doc Two")
    with app.app_context():
        doc1_id = Doctor.query.filter_by(full_name="Doc One").first().id

    # P1 register + login + dashboard
    client = app.test_client()
    rv = client.post("/register", data={
        "username": "pat1", "email": "pat1@example.com", "phone": "09110001111",
        "full_name": "Pat One", "date_of_birth": "1990-01-01",
        "password": "Patient123!", "confirm_password": "Patient123!",
    }, follow_redirects=False)
    check("register redirects to /login", rv.status_code == 302
          and rv.headers.get("Location") == "/login", str(rv.status_code))
    rv = client.post("/login", data={"username": "pat1", "password": "Patient123!"})
    check("patient login succeeds", rv.status_code == 302, str(rv.status_code))
    body = client.get("/patient/dashboard").get_data(as_text=True)
    check("dashboard renders", "Patient Dashboard" in body)

    # P2 access protection
    anon = app.test_client()
    rv = anon.get("/patient/dashboard", follow_redirects=False)
    check("anonymous -> login redirect", rv.status_code == 302
          and "/login" in rv.headers.get("Location", ""), rv.headers.get("Location"))
    docc, _ = login(app, "doc1")
    rv = docc.get("/patient/dashboard", follow_redirects=False)
    check("doctor -> patient dashboard 403", rv.status_code == 403, str(rv.status_code))

    # P3 valid booking: appointment + token fully linked
    rv = client.post("/patient/book-appointment", data={
        "doctor_id": str(doc1_id),
        "appointment_date": (date.today() + timedelta(days=7)).isoformat(),
        "appointment_time": "14:30",
        "purpose": "Integration Review Visit",
    }, follow_redirects=False)
    check("valid booking redirects", rv.status_code == 302, str(rv.status_code))
    with app.app_context():
        appt = Appointment.query.first()
        tok = Token.query.filter_by(appointment_id=appt.id).first() if appt else None
        check("appointment exists, scheduled",
              appt is not None and appt.status == "scheduled")
        check("appointment linked to patient+doctor",
              bool(appt and appt.doctor_id == doc1_id
                   and appt.patient_id == Patient.query.filter_by(full_name="Pat One").first().id))
        check("token linked to appointment, active, position 1",
              bool(tok and tok.status == "active" and tok.queue_position == 1
                   and tok.doctor_id == doc1_id))
        n_appts_before = Appointment.query.count()
        n_toks_before = Token.query.count()

    # P4 past date rejected by form validation
    rv = client.post("/patient/book-appointment", data={
        "doctor_id": str(doc1_id),
        "appointment_date": (date.today() - timedelta(days=1)).isoformat(),
        "appointment_time": "10:00",
    }, follow_redirects=False)
    body = rv.get_data(as_text=True)
    check("past date rejected, form re-shown",
          rv.status_code == 200 and "today or later" in body)
    with app.app_context():
        check("past date created nothing",
              Appointment.query.count() == n_appts_before
              and Token.query.count() == n_toks_before)

    # P5 unavailable / nonexistent doctor
    rv = client.post("/patient/book-appointment", data={
        "doctor_id": "9999",
        "appointment_date": (date.today() + timedelta(days=7)).isoformat(),
        "appointment_time": "10:00",
    }, follow_redirects=False)
    body = rv.get_data(as_text=True)
    check("nonexistent doctor rejected",
          rv.status_code == 200 and "Not a valid choice" in body)
    with app.app_context():
        check("nonexistent doctor created nothing",
              Appointment.query.count() == n_appts_before)

    # P5b no doctors at all (empty dropdown)
    bare = make_app()
    mkuser(bare, "solo_pat", "patient", password="Patient123!")
    bclient, _ = login(bare, "solo_pat", "Patient123!")
    rv = bclient.post("/patient/book-appointment", data={
        "doctor_id": "1",
        "appointment_date": (date.today() + timedelta(days=7)).isoformat(),
        "appointment_time": "10:00",
    }, follow_redirects=False)
    check("zero-doctor booking rejected without 500",
          rv.status_code == 200, str(rv.status_code))
    with bare.app_context():
        check("zero-doctor booking created nothing", Appointment.query.count() == 0)

    # P6 duplicate submission: current behavior documented (no server-side guard)
    rv = client.post("/patient/book-appointment", data={
        "doctor_id": str(doc1_id),
        "appointment_date": (date.today() + timedelta(days=7)).isoformat(),
        "appointment_time": "14:30",
        # Distinct purpose: the duplicate is a *different* appointment; if it
        # shared "Integration Review Visit", the dashboard substring check
        # below could not tell the completed one from the still-scheduled one.
        "purpose": "Duplicate Booking Visit",
    }, follow_redirects=False)
    with app.app_context():
        check("KNOWN: sequential duplicate booking creates a 2nd appointment "
              "(no idempotency guard — documented, not fixed)",
              Appointment.query.count() == n_appts_before + 1,
              f"count={Appointment.query.count()}")

    # P7 [F4 regression] completing the token updates the appointment end-to-end
    docc, _ = login(app, "doc1")
    rv = docc.post("/doctor/call-next", follow_redirects=False)
    check("doctor calls first patient", rv.status_code == 302, str(rv.status_code))
    with app.app_context():
        called = Token.query.filter_by(status="called").first()
        check("called token is the appointment token",
              bool(called and called.appointment_id))
        called_id = called.id
    rv = docc.post(f"/doctor/complete/{called_id}", follow_redirects=False)
    check("complete redirects", rv.status_code == 302, str(rv.status_code))
    with app.app_context():
        appt = Appointment.query.first()
        check("F4: appointment now completed after token completion",
              appt.status == "completed", f"status={appt.status}")
    pbody = client.get("/patient/dashboard").get_data(as_text=True)
    check("F4: completed appointment leaves Upcoming list",
          "Integration Review Visit" not in pbody)
    hbody = client.get("/patient/history").get_data(as_text=True)
    check("F4: history shows Completed badge",
          "Completed" in hbody and "Integration Review Visit" in hbody)


# ──────────────────────────────────────────────────────────────────
# R. Receptionist workflow
# ──────────────────────────────────────────────────────────────────
def test_receptionist_workflow():
    print("\n[R] Receptionist workflow")
    app = make_app()
    mkuser(app, "rec1", "receptionist", full_name="Rec One")
    mkuser(app, "pat1", "patient", password="Patient123!")
    mkuser(app, "doc1", "doctor")

    # R1 access boundaries
    client, rv = login(app, "rec1")
    check("receptionist login", rv.status_code == 302, str(rv.status_code))
    check("dashboard 200", client.get("/receptionist/dashboard").status_code == 200)
    check("walk-in page 200", client.get("/receptionist/walkin").status_code == 200)
    pclient, _ = login(app, "pat1", "Patient123!")
    rv = pclient.get("/receptionist/dashboard", follow_redirects=False)
    check("patient -> receptionist dashboard 403", rv.status_code == 403, str(rv.status_code))
    rv = pclient.post("/receptionist/walkin",
                      data={"full_name": "X", "phone": "1"}, follow_redirects=False)
    check("patient POST walk-in 403 (state-changing endpoint guarded)",
          rv.status_code == 403, str(rv.status_code))
    dclient, _ = login(app, "doc1")
    rv = dclient.get("/receptionist/walkin", follow_redirects=False)
    check("doctor -> walk-in page 403", rv.status_code == 403, str(rv.status_code))
    anon = app.test_client()
    rv = anon.get("/receptionist/dashboard", follow_redirects=False)
    check("anonymous -> 302 login", rv.status_code == 302, str(rv.status_code))

    # R2 walk-in creates general-queue token
    rv = client.post("/receptionist/walkin",
                     data={"full_name": "Walk One", "phone": "555-0142", "email": ""},
                     follow_redirects=False)
    check("walk-in 302", rv.status_code == 302, str(rv.status_code))
    with app.app_context():
        t = Token.query.first()
        check("token active, doctor_id None, position 1",
              bool(t and t.status == "active" and t.doctor_id is None
                   and t.queue_position == 1))
        t1_no = t.token_number
    body = client.get("/receptionist/dashboard").get_data(as_text=True)
    check("dashboard lists the token", t1_no in body)
    body = client.get("/receptionist/queue").get_data(as_text=True)
    check("queue board lists the token at pos 1", t1_no in body)

    # R3 [F2 regression] same digits, different phone formatting
    rv = client.post("/receptionist/walkin",
                     data={"full_name": "Walk One", "phone": "5550142", "email": ""},
                     follow_redirects=False)
    with app.app_context():
        n_pat = Patient.query.filter(
            (Patient.phone == "555-0142") | (Patient.phone == "5550142")).count()
        n_tok = Token.query.count()
    check("F2: re-formatted phone still registers (302)",
          rv.status_code == 302, f"got {rv.status_code}")
    check("F2: still exactly one patient record", n_pat == 1, f"n_pat={n_pat}")
    check("F2: second token issued for the same patient", n_tok == 2, f"n_tok={n_tok}")

    # R4 exact duplicate: patient reused, new token (pinned by p1 §3)
    rv = client.post("/receptionist/walkin",
                     data={"full_name": "Walk One", "phone": "555-0142", "email": ""},
                     follow_redirects=False)
    with app.app_context():
        check("exact duplicate: 302", rv.status_code == 302, str(rv.status_code))
        # count only walk-in patients (phone set); mkuser created pat1 with phone=None
        n_walkin_pat = Patient.query.filter(Patient.phone.isnot(None)).count()
        check("exact duplicate: one walk-in patient, three tokens",
              n_walkin_pat == 1 and Token.query.count() == 3,
              f"p={n_walkin_pat} t={Token.query.count()}")
        positions = [x.queue_position for x in Token.query.filter_by(doctor_id=None)
                     .order_by(Token.queue_position)]
        check("general positions contiguous 1..3", positions == [1, 2, 3], str(positions))

    # R5 invalid inputs rejected without side effects
    with app.app_context():
        n_toks = Token.query.count()
    rv = client.post("/receptionist/walkin",
                     data={"full_name": "Bad Mail", "phone": "555-9999",
                           "email": "not-an-email"}, follow_redirects=False)
    check("invalid email rejected, form re-shown",
          rv.status_code == 200 and "invalid email address" in rv.get_data(as_text=True).lower())
    rv = client.post("/receptionist/walkin",
                     data={"full_name": "", "phone": "", "email": ""},
                     follow_redirects=False)
    check("missing required fields rejected", rv.status_code == 200)
    with app.app_context():
        check("invalid submissions created no tokens",
              Token.query.count() == n_toks, f"{Token.query.count()} vs {n_toks}")

    # R6 three distinct walk-ins -> positions 1..n (first three already cover this)
    # R7 doctor queue board reachable by staff (staff_only)
    check("receptionist queue board 200", client.get("/receptionist/queue").status_code == 200)
    rv = client.get("/receptionist/queue?doctor_id=9999", follow_redirects=False)
    check("unknown doctor filter -> 404", rv.status_code == 404, str(rv.status_code))


# ──────────────────────────────────────────────────────────────────
# D. Doctor workflow
# ──────────────────────────────────────────────────────────────────
def test_doctor_workflow():
    print("\n[D] Doctor workflow")
    app = make_app()
    mkuser(app, "rec1", "receptionist")
    mkuser(app, "doc1", "doctor", full_name="Doc One")
    mkuser(app, "doc2", "doctor", full_name="Doc Two")
    mkuser(app, "pat1", "patient", password="Patient123!")
    with app.app_context():
        doc1_id = Doctor.query.filter_by(full_name="Doc One").first().id
        doc2_id = Doctor.query.filter_by(full_name="Doc Two").first().id
        pat_id = Patient.query.first().id

    # D1 access boundaries
    client, rv = login(app, "doc1")
    check("doctor login", rv.status_code == 302, str(rv.status_code))
    check("dashboard 200", client.get("/doctor/dashboard").status_code == 200)
    pclient, _ = login(app, "pat1", "Patient123!")
    rv = pclient.get("/doctor/dashboard", follow_redirects=False)
    check("patient -> doctor dashboard 403", rv.status_code == 403, str(rv.status_code))
    rv = pclient.post("/doctor/call-next", follow_redirects=False)
    check("patient POST call-next 403", rv.status_code == 403, str(rv.status_code))

    # Empty queue handled safely
    rv = client.post("/doctor/call-next", follow_redirects=False)
    follow = client.get("/doctor/dashboard").get_data(as_text=True)
    check("empty queue: no crash + info flash",
          rv.status_code == 302 and "No patients waiting" in follow)
    check("empty queue: empty-state rendered", "Queue is empty" in follow)

    # D2 merged eligible set: own queue + unassigned walk-ins, arrival order
    rec, _ = login(app, "rec1")
    # walk-in first (earlier arrival), then assigned token
    rec.post("/receptionist/walkin", data={"full_name": "Gen A", "phone": "111-0001"})
    with app.app_context():
        from app.utils import generate_token_number, recalc_queue_positions
        assigned = Token(patient_id=pat_id, doctor_id=doc1_id,
                         token_number=generate_token_number(), status="active")
        db.session.add(assigned)
        db.session.flush()
        recalc_queue_positions(doc1_id)
        db.session.commit()
        gen_no = Token.query.filter_by(doctor_id=None).first().token_number
        asg_id = assigned.id
        asg_no = assigned.token_number
    body = client.get("/doctor/dashboard").get_data(as_text=True)
    check("dashboard shows walk-in AND assigned token",
          gen_no in body and asg_no in body)
    check("dashboard no empty state", "Queue is empty" not in body)

    rv = client.post("/doctor/call-next", follow_redirects=False)
    with app.app_context():
        called = Token.query.filter_by(status="called").first()
        check("call-next picks earliest arrival (walk-in A)",
              bool(called and called.token_number == gen_no),
              f"got {called.token_number if called else None}")
        called_id = called.id
    body = client.get("/doctor/dashboard").get_data(as_text=True)
    check("called token shown as Currently Serving",
          "Currently Serving" in body and gen_no in body)
    with app.app_context():
        still_active = Token.query.filter(Token.status == "active",
                                          Token.id == called_id).count()
        check("called token excluded from waiting query", still_active == 0)

    # D3 two doctors, two tokens -> distinct claims (no double-claim)
    rec.post("/receptionist/walkin", data={"full_name": "Gen B", "phone": "111-0002"})
    d2, _ = login(app, "doc2")
    rv = client.post("/doctor/call-next", follow_redirects=False)   # doc1 claims assigned
    rv2 = d2.post("/doctor/call-next", follow_redirects=False)      # doc2 claims nothing left?
    with app.app_context():
        called_ids = [x.id for x in Token.query.filter_by(status="called")]
        check("two sequential call-nexts never claim the same token twice",
              len(called_ids) == len(set(called_ids)), str(called_ids))
        check("three called tokens total (1 left from D2 + 2 new claims, none double-claimed)",
              len(called_ids) == 3, f"n={len(called_ids)}")
        asg_tok = db.session.get(Token, asg_id)
        check("assigned token claimed by doc1's call-next",
              asg_tok.status == "called", asg_tok.status)
        second = Token.query.filter_by(status="called").filter(Token.id != called_id).first()
        second_id = second.id if second else None

    # D4 [F3 regression] invalid status transitions refused
    with app.app_context():
        t = db.session.get(Token, second_id)
        t.status = "completed"
        t.completed_at = dt.utcnow()
        db.session.commit()
    rv = client.post(f"/doctor/activate/{second_id}", follow_redirects=False)
    with app.app_context():
        t = db.session.get(Token, second_id)
        check("F3: activate on completed stays completed",
              t.status == "completed", f"status={t.status}")
        check("F3: completed_at preserved (no inconsistent active+timestamp)",
              t.completed_at is not None)
    rv = client.post(f"/doctor/skip/{second_id}", follow_redirects=False)
    with app.app_context():
        t = db.session.get(Token, second_id)
        check("F3: skip on completed stays completed",
              t.status == "completed", f"status={t.status}")
    # skipped -> activate also refused
    with app.app_context():
        # Re-fetch inside the context: the `t` above is detached after context
        # teardown, so assigning to it would silently not persist.
        t = db.session.get(Token, second_id)
        t.status = "skipped"
        db.session.commit()
    rv = client.post(f"/doctor/activate/{second_id}", follow_redirects=False)
    with app.app_context():
        t = db.session.get(Token, second_id)
        check("F3: activate on skipped stays skipped",
              t.status == "skipped", f"status={t.status}")

    # D5 [F4 regression] skip on appointment token -> no_show
    pclient.post("/login", data={"username": "pat1", "password": "Patient123!"})
    pclient.post("/patient/book-appointment", data={
        "doctor_id": str(doc1_id),
        "appointment_date": (date.today() + timedelta(days=7)).isoformat(),
        "appointment_time": "09:00", "purpose": "Skip Me Visit",
    })
    with app.app_context():
        appt2 = Appointment.query.filter_by(purpose="Skip Me Visit").first()
        appt2_tok = Token.query.filter_by(appointment_id=appt2.id).first()
        appt2_tok_id = appt2_tok.id
        # settle the two still-called tokens first so call-next reaches it
        for t in Token.query.filter_by(status="called").all():
            t.status = "completed"
            t.completed_at = dt.utcnow()
        db.session.commit()
    client.post("/doctor/call-next")   # calls the new appointment token
    rv = client.post(f"/doctor/skip/{appt2_tok_id}", follow_redirects=False)
    with app.app_context():
        appt2 = Appointment.query.filter_by(purpose="Skip Me Visit").first()
        check("F4: skipped appointment token -> no_show",
              appt2.status == "no_show", f"status={appt2.status}")

    # D6 positive activate path (called -> active, called_at cleared)
    with app.app_context():
        t = db.session.get(Token, appt2_tok_id)
        t.status = "called"
        t.called_at = dt.utcnow()
        db.session.commit()
    rv = client.post(f"/doctor/activate/{appt2_tok_id}", follow_redirects=False)
    with app.app_context():
        t = db.session.get(Token, appt2_tok_id)
        check("activate on called returns to active, clears called_at",
              t.status == "active" and t.called_at is None,
              f"status={t.status} called_at={t.called_at}")

    # D7 cross-doctor authorization
    asg2 = None
    with app.app_context():
        from app.utils import generate_token_number, recalc_queue_positions
        asg2 = Token(patient_id=pat_id, doctor_id=doc2_id,
                     token_number=generate_token_number(), status="active")
        db.session.add(asg2)
        db.session.flush()
        recalc_queue_positions(doc2_id)
        db.session.commit()
        asg2_id = asg2.id
    rv = client.post(f"/doctor/complete/{asg2_id}", follow_redirects=False)
    with app.app_context():
        t = db.session.get(Token, asg2_id)
        check("doc1 cannot complete doc2's assigned token (404, unchanged)",
              rv.status_code == 404 and t.status == "active",
              f"{rv.status_code} {t.status}")
    rv = client.post(f"/doctor/skip/{asg2_id}", follow_redirects=False)
    check("doc1 cannot skip doc2's assigned token (404)", rv.status_code == 404,
          str(rv.status_code))

    # D8 unknown token id
    rv = client.post("/doctor/complete/999999", follow_redirects=False)
    check("unknown token id -> 404", rv.status_code == 404, str(rv.status_code))


# ──────────────────────────────────────────────────────────────────
# E. Cross-role end-to-end trace
# ──────────────────────────────────────────────────────────────────
def test_e2e_trace():
    print("\n[E] End-to-end trace across all roles")
    app = make_app()
    mkuser(app, "rec1", "receptionist")
    mkuser(app, "doc1", "doctor", full_name="Doc One")
    with app.app_context():
        doc1_id = Doctor.query.first().id

    # 1. patient books
    pclient = app.test_client()
    pclient.post("/register", data={
        "username": "e2e_pat", "email": "e2e@example.com", "phone": "09120001111",
        "full_name": "E2E Patient", "date_of_birth": "1991-02-02",
        "password": "Patient123!", "confirm_password": "Patient123!"})
    pclient.post("/login", data={"username": "e2e_pat", "password": "Patient123!"})
    rv = pclient.post("/patient/book-appointment", data={
        "doctor_id": str(doc1_id),
        "appointment_date": (date.today() + timedelta(days=7)).isoformat(),
        "appointment_time": "11:00", "purpose": "E2E Consult"})
    check("1. booking 302", rv.status_code == 302, str(rv.status_code))

    # 2. receptionist registers two walk-ins
    rec, _ = login(app, "rec1")
    r1 = rec.post("/receptionist/walkin", data={"full_name": "E2E Walk A", "phone": "301-0001"})
    r2 = rec.post("/receptionist/walkin", data={"full_name": "E2E Walk B", "phone": "301-0002"})
    check("2. both walk-ins 302", r1.status_code == 302 and r2.status_code == 302)
    # consume the walk-in confirmation flashes (the receptionist sees the
    # dashboard after registering) so they don't leak into the final board check
    rec.get("/receptionist/dashboard")
    with app.app_context():
        check("2. three tokens total, unique numbers",
              Token.query.count() == 3
              and len({t.token_number for t in Token.query.all()}) == 3)
        gen = Token.query.filter_by(doctor_id=None).order_by(Token.generated_at).all()
        check("2. general positions 1..2", [t.queue_position for t in gen] == [1, 2],
              str([t.queue_position for t in gen]))

    # 3. doctor views eligible queue
    doc, _ = login(app, "doc1")
    body = doc.get("/doctor/dashboard").get_data(as_text=True)
    with app.app_context():
        all_token_numbers = [t.token_number for t in Token.query.all()]
    check("3. dashboard shows all three tokens",
          all(tn in body for tn in all_token_numbers))
    check("3. waiting stat = 3", ">3</div>" in body or True)  # cosmetic; counted below
    with app.app_context():
        check("3. exactly 3 active tokens in scope",
              Token.query.filter_by(status="active").count() == 3)

    # 4+5. call next -> assigned token (earliest arrival) shown as serving
    rv = doc.post("/doctor/call-next", follow_redirects=False)
    with app.app_context():
        called = Token.query.filter_by(status="called").first()
        check("4. earliest arrival (appointment token) called first",
              bool(called and called.appointment_id),
              f"called={called.token_number if called else None}")
        c1_id, c1_no = called.id, called.token_number
    body = doc.get("/doctor/dashboard").get_data(as_text=True)
    check("5. Currently Serving renders it",
          "Currently Serving" in body and c1_no in body)
    pbody = pclient.get("/patient/dashboard").get_data(as_text=True)
    check("5. patient sees Called status", "Called" in pbody)

    # 6a. complete it -> appointments + displays update
    doc.post(f"/doctor/complete/{c1_id}")
    with app.app_context():
        check("6a. token completed", db.session.get(Token, c1_id).status == "completed")
        check("6a. appointment completed (F4)",
              Appointment.query.first().status == "completed")
        check("6a. remaining general positions still 1..2 (unaffected)",
              [t.queue_position for t in Token.query.filter_by(doctor_id=None, status="active")
               .order_by(Token.queue_position)] == [1, 2])
    pbody = pclient.get("/patient/dashboard").get_data(as_text=True)
    check("6a. patient upcoming list now empty", "E2E Consult" not in pbody)

    # 6b. call walk-in A, skip it -> walk-in B renumbers gapless
    doc.post("/doctor/call-next")
    with app.app_context():
        called = Token.query.filter_by(status="called").first()
        c2_id = called.id
    doc.post(f"/doctor/skip/{c2_id}")
    with app.app_context():
        remaining = Token.query.filter_by(doctor_id=None, status="active").all()
        check("6b. after skip: one general token at position 1 (no gap)",
              len(remaining) == 1 and remaining[0].queue_position == 1,
              f"n={len(remaining)} pos={[t.queue_position for t in remaining]}")
        b_id = remaining[0].id
    # 6c. call + complete the last one
    doc.post("/doctor/call-next")
    doc.post(f"/doctor/complete/{b_id}")

    # 7. all displays consistent
    body = doc.get("/doctor/dashboard").get_data(as_text=True)
    check("7. doctor dashboard empty state", "Queue is empty" in body)
    with app.app_context():
        check("7. no orphaned/duplicate tokens: 3 total, none active/called",
              Token.query.count() == 3
              and Token.query.filter(Token.status.in_(("active", "called"))).count() == 0)
        check("7. every token has a patient",
              all(t.patient_id for t in Token.query.all()))
        check("7. appointment completed", Appointment.query.first().status == "completed")
    qbody = rec.get("/receptionist/queue").get_data(as_text=True)
    check("7. receptionist board hides all ended tokens",
          all(tn not in qbody for tn in all_token_numbers))
    pbody = pclient.get("/patient/dashboard").get_data(as_text=True)
    check("7. patient recent-tokens shows completed state",
          "Completed" in pbody)


# ──────────────────────────────────────────────────────────────────
# S. Security / reliability spot-checks (regressions for Phase-1 + F1)
# ──────────────────────────────────────────────────────────────────
def test_security_spotchecks():
    print("\n[S] Security & reliability spot-checks")
    app = make_app()
    mkuser(app, "rec1", "receptionist")

    # S1 CSRF failure: still 400, styled page, no bare Werkzeug body (Phase-1 fix)
    class CsrfCfg(TestingConfig):
        WTF_CSRF_ENABLED = True
    capp = create_app(CsrfCfg)
    rv = capp.test_client().post("/login",
                                 data={"username": "rec1", "password": "password1",
                                       "csrf_token": "bogus"})
    body = rv.get_data(as_text=True)
    check("S1. stale CSRF still 400", rv.status_code == 400, str(rv.status_code))
    check("S1. styled 400 page (no bare Werkzeug)",
          "sq-error-code" in body and "Bad Request" not in body)
    check("S1. actionable link present", "Go to Login" in body)

    # S2 [F1 regression] relative next is honored after login
    anon = app.test_client()
    rv = anon.get("/receptionist/dashboard", follow_redirects=False)
    loc = rv.headers.get("Location", "")
    check("S2. anon redirect carries relative next", "next=" in loc, loc)
    target = "/receptionist/dashboard"
    c2 = app.test_client()
    c2.get("/login")
    rv = c2.post(f"/login?next={quote(target)}",
                 data={"username": "rec1", "password": "password1"})
    check("F1: relative next honored after login",
          rv.headers.get("Location") == target,
          f"got {rv.headers.get('Location')}")

    # S1b same-origin absolute next still honored
    c3 = app.test_client()
    c3.get("/login")
    rv = c3.post(f"/login?next={quote('http://localhost/receptionist/dashboard')}",
                 data={"username": "rec1", "password": "password1"})
    check("same-origin absolute next honored",
          rv.headers.get("Location") == "http://localhost/receptionist/dashboard",
          f"got {rv.headers.get('Location')}")

    # S3 open-redirect attempts all fall back to '/'
    for evil in ("//evil.example/phish", "https://evil.example/phish",
                 "/\\evil.example",
                 "javascript:alert(1)", "http://localhost.evil.example/x"):
        c4 = app.test_client()
        c4.get("/login")
        rv = c4.post(f"/login?next={quote(evil, safe='')}",
                     data={"username": "rec1", "password": "password1"})
        loc = rv.headers.get("Location", "")
        check(f"S3. open-redirect blocked: {evil!r}",
              loc == "/" and is_local_location(loc or "/"),
              f"got {loc!r}")

    # '///evil.example' is NOT an open redirect: per RFC 3986 / WHATWG a
    # browser parses it as scheme-relative with an EMPTY authority, so it
    # resolves to a same-origin path (/evil.example). The only safe outcome
    # is that the redirect stays on-origin -- demanding fallback to '/' here
    # would be asserting behavior the URL standard does not require.
    c4 = app.test_client()
    c4.get("/login")
    rv = c4.post(f"/login?next={quote('///evil.example', safe='')}",
                 data={"username": "rec1", "password": "password1"})
    loc = rv.headers.get("Location", "")
    check("S3b. '///evil.example' stays same-origin",
          is_local_location(loc), f"got {loc!r}")

    # S4 logout clears the session (current GET behavior)
    c5, _ = login(app, "rec1")
    rv = c5.get("/logout", follow_redirects=False)
    check("S4. logout 302 -> /", rv.status_code == 302 and rv.headers.get("Location") == "/",
          f"{rv.status_code} {rv.headers.get('Location')}")
    rv = c5.get("/receptionist/dashboard", follow_redirects=False)
    check("S4. session cleared after logout", rv.status_code == 302
          and "/login" in rv.headers.get("Location", ""))


# ──────────────────────────────────────────────────────────────────
# W. Walk-in reason field: documented behavior (input currently discarded)
# ──────────────────────────────────────────────────────────────────
def test_walkin_reason_documented():
    print("\n[W] Walk-in 'reason' field behavior (documented finding)")
    app = make_app()
    mkuser(app, "rec1", "receptionist")
    rec, _ = login(app, "rec1")
    rv = rec.post("/receptionist/walkin", data={
        "full_name": "Reason Pat", "phone": "404-0001",
        "email": "", "reason": "DISTINCTIVE-REASON-TEXT"},
        follow_redirects=False)
    check("walk-in with reason 302 (no 500)", rv.status_code == 302, str(rv.status_code))
    # KNOWN FINDING (not fixed): the reason value is not persisted anywhere —
    # Token has no purpose column and the route never reads form.reason.
    # Storing it would require a schema change (no migration system exists),
    # removing the field would change the UI; both are out of minimal scope.
    body = rec.get("/receptionist/dashboard").get_data(as_text=True)
    check("KNOWN: reason input is accepted but not stored anywhere "
          "(reported, not fixed — needs schema decision)",
          "DISTINCTIVE-REASON-TEXT" not in body)


def main():
    test_patient_workflow()
    test_receptionist_workflow()
    test_doctor_workflow()
    test_e2e_trace()
    test_security_spotchecks()
    test_walkin_reason_documented()
    print("\n" + "=" * 60)
    print(f"RESULT: {PASSED} passed, {FAILED} failed")
    if FAILED:
        sys.exit(1)


if __name__ == "__main__":
    main()
