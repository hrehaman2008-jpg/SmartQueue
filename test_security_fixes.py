"""Regression tests for the approved security fixes.

Covers:
- H2: production refuses to boot with a missing/default FLASK_SECRET_KEY,
  and the failure message never echoes any secret value.
- H1: demo seeding fails closed in production (no published default
  passwords; explicit DEMO_*_PASSWORD env vars required per account),
  while non-production keeps the documented development defaults.
- M1: /qr/<n> and /api/token/<n> require authentication and role/ownership
  authorization; responses never contain patient details.
- M2: CSRF is enforced globally — state-changing POSTs without a valid
  token are rejected, and valid-token requests still work.

Uses isolated in-memory databases only — never touches instance/queue.db
or any production database. Run with:

    venv\\Scripts\\python test_security_fixes.py
"""

import os
import re
import sys
from importlib import reload

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app, db, seed_demo_accounts
from app.config import BaseConfig, ProductionConfig, TestingConfig
from app.models import User, Patient, Doctor, Receptionist, Token

PASS = 0
FAIL = 0

# Environment keys this suite mutates — restored after every section.
ENV_KEYS = [
    "FLASK_CONFIG", "FLASK_SECRET_KEY", "DATABASE_URL",
    "ENABLE_DEMO_SEEDING",
    "DEMO_DOCTOR_PASSWORD", "DEMO_RECEPTIONIST_PASSWORD", "DEMO_PATIENT_PASSWORD",
]
_SAVED_ENV = {k: os.environ.get(k) for k in ENV_KEYS}


def restore_env():
    for key, value in _SAVED_ENV.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}  {detail}")


def clear_env():
    for key in ENV_KEYS:
        os.environ.pop(key, None)


def make_user(app, username, role, password="password1", **profile):
    with app.app_context():
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


def login(client, username, password):
    return client.post("/login", data={"username": username, "password": password},
                       follow_redirects=False)


def csrf_from(html):
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1) if m else None


def production_app(**extra_env):
    """Boot an app exactly as production would: FLASK_CONFIG=production."""
    clear_env()
    os.environ["FLASK_CONFIG"] = "production"
    # In-memory DB: production-config tests must never touch a file DB.
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    for key, value in extra_env.items():
        os.environ[key] = value
    # BaseConfig evaluates env vars at import time; reloading simulates
    # "environment present before process start" (same technique as
    # test_seeding_fixed.py).
    import app.config
    reload(app.config)
    return create_app()


# ──────────────────────────────────────────────────────────────────
# H2: production SECRET_KEY guard
# ──────────────────────────────────────────────────────────────────
def test_production_secret_guard():
    print("\n[H2] Production SECRET_KEY startup guard")
    try:
        # a) Missing secret -> clear startup failure, no secret echoed
        clear_env()
        os.environ["FLASK_CONFIG"] = "production"
        os.environ["DATABASE_URL"] = "sqlite:///:memory:"
        try:
            app = production_app()
            check("production without FLASK_SECRET_KEY refuses to boot",
                  False, f"create_app returned {app!r}")
        except RuntimeError as exc:
            msg = str(exc)
            check("production without FLASK_SECRET_KEY refuses to boot", True)
            check("error names FLASK_SECRET_KEY", "FLASK_SECRET_KEY" in msg)
            check("error does not echo the dev default",
                  BaseConfig.DEV_SECRET_KEY not in msg)
            check("error does not echo any configured secret",
                  "s3cr3t-sentinel-value" not in msg)
        except Exception as exc:  # noqa: BLE001
            check("production without FLASK_SECRET_KEY refuses to boot",
                  False, f"raised {type(exc).__name__}: {exc}")

        # b) Explicit secret -> boots fine (secret value never printed)
        app = production_app(FLASK_SECRET_KEY="s3cr3t-sentinel-value")
        check("production with explicit FLASK_SECRET_KEY boots",
              app is not None)
        check("configured secret loaded into app config",
              app.config.get("SECRET_KEY") == "s3cr3t-sentinel-value")

        # c) Passing ProductionConfig directly is guarded too
        clear_env()
        os.environ["DATABASE_URL"] = "sqlite:///:memory:"
        try:
            create_app(ProductionConfig)
            check("ProductionConfig class directly is also guarded",
                  False, "create_app did not raise")
        except RuntimeError:
            check("ProductionConfig class directly is also guarded", True)

        # d) Development/testing keeps its convenient fallback
        clear_env()
        os.environ["DATABASE_URL"] = "sqlite:///:memory:"
        app = create_app(TestingConfig)
        check("testing config still boots without FLASK_SECRET_KEY",
              app is not None)
    finally:
        restore_env()


# ──────────────────────────────────────────────────────────────────
# H1: demo seeding fails closed in production
# ──────────────────────────────────────────────────────────────────
def test_production_seeding_fail_closed():
    print("\n[H1] Demo seeding fails closed in production")
    try:
        # a) Flag on, no DEMO_*_PASSWORD vars -> NOTHING is seeded
        app = production_app(
            FLASK_SECRET_KEY="s3cr3t-sentinel-value",
            ENABLE_DEMO_SEEDING="1",
        )
        with app.app_context():
            n_users = User.query.count()
            check("production seeding without DEMO_*_PASSWORD creates nothing",
                  n_users == 0, f"got {n_users} users")
            check("no default-password doctor exists",
                  User.query.filter_by(username="doctor_demo").first() is None)
            check("no default-password patient exists",
                  User.query.filter_by(username="patient_demo").first() is None)

        # b) Explicit env password -> that one account is seeded with it,
        #    and never with the published default.
        app = production_app(
            FLASK_SECRET_KEY="s3cr3t-sentinel-value",
            ENABLE_DEMO_SEEDING="1",
            DEMO_DOCTOR_PASSWORD="ExplicitOperatorPw!99",
        )
        with app.app_context():
            doc = User.query.filter_by(username="doctor_demo").first()
            check("explicitly configured demo doctor is seeded",
                  doc is not None and doc.role == "doctor")
            check("seeded doctor matches the explicit password",
                  bool(doc and doc.check_password("ExplicitOperatorPw!99")))
            check("seeded doctor does NOT match the published default",
                  bool(doc and not doc.check_password("Doctor123!")))
            check("receptionist skipped (no explicit password)",
                  User.query.filter_by(username="reception_demo").first() is None)
            check("patient skipped (no explicit password)",
                  User.query.filter_by(username="patient_demo").first() is None)
            check("doctor profile created alongside the account",
                  bool(doc and doc.doctor is not None))

        # c) Calling seed_demo_accounts directly with a production-style
        #    app must apply the same fail-closed rule.
        app = production_app(FLASK_SECRET_KEY="s3cr3t-sentinel-value")
        with app.app_context():
            seed_demo_accounts(app)
            check("direct seed_demo_accounts call also fails closed",
                  User.query.count() == 0, f"got {User.query.count()}")

        # d) Non-production keeps the documented dev defaults
        clear_env()
        os.environ["DATABASE_URL"] = "sqlite:///:memory:"
        os.environ["ENABLE_DEMO_SEEDING"] = "1"
        app = create_app(TestingConfig)
        with app.app_context():
            doc = User.query.filter_by(username="doctor_demo").first()
            check("non-production seeding still uses dev defaults",
                  bool(doc and doc.check_password("Doctor123!")))
            check("all three demo accounts seeded in non-production",
                  all(User.query.filter_by(username=u).first() is not None
                      for u in ("doctor_demo", "reception_demo", "patient_demo")))
    finally:
        restore_env()


# ──────────────────────────────────────────────────────────────────
# M1: token + QR endpoint privacy and authorization
# ──────────────────────────────────────────────────────────────────
def test_token_qr_privacy():
    print("\n[M1] /qr and /api/token authorization")
    clear_env()
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    app = create_app(TestingConfig)

    make_user(app, "doc1", "doctor")
    make_user(app, "doc2", "doctor")
    make_user(app, "rec1", "receptionist")
    p1_id = make_user(app, "pat1", "patient", full_name="SECRET NAME ONE",
                      phone="555-0111")
    p2_id = make_user(app, "pat2", "patient", full_name="SECRET NAME TWO",
                      phone="555-0222")

    with app.app_context():
        d1 = Doctor.query.filter_by(full_name="Doc1").first()
        d2 = Doctor.query.filter_by(full_name="Doc2").first()
        p1 = Patient.query.filter_by(full_name="SECRET NAME ONE").first()
        p2 = Patient.query.filter_by(full_name="SECRET NAME TWO").first()
        t_a = Token(patient_id=p1.id, doctor_id=d1.id, token_number="T-1001",
                    status="active", queue_position=1)
        t_b = Token(patient_id=p2.id, doctor_id=d2.id, token_number="T-1002",
                    status="active", queue_position=1)
        t_w = Token(patient_id=p2.id, doctor_id=None, token_number="T-1003",
                    status="active", queue_position=1)
        db.session.add_all([t_a, t_b, t_w])
        db.session.commit()

    anon = app.test_client()
    for path in ("/qr/T-1001", "/api/token/T-1001"):
        resp = anon.get(path)
        loc = resp.headers.get("Location", "")
        check(f"anonymous GET {path} -> redirect to login",
              resp.status_code == 302 and loc.startswith("/login"),
              f"got {resp.status_code} Location={loc!r}")

    # Owner patient: own token only
    c1 = app.test_client()
    login(c1, "pat1", "password1")
    resp = c1.get("/qr/T-1001")
    check("owner patient gets QR image (200, PNG)",
          resp.status_code == 200 and resp.get_data()[:4] == b"\x89PNG",
          f"got {resp.status_code}")
    resp = c1.get("/api/token/T-1001")
    data = resp.get_json() or {}
    check("owner patient gets token status JSON",
          resp.status_code == 200 and data.get("token") == "T-1001",
          f"got {resp.status_code} {data}")
    body = resp.get_data(as_text=True)
    check("response contains no patient name or phone",
          "SECRET" not in body and "555-0111" not in body and "555-0222" not in body,
          body)
    check("response keys limited to safe fields",
          set(data.keys()) <= {"token", "position", "status", "doctor", "message"},
          f"got {sorted(data.keys())}")
    for other in ("/qr/T-1002", "/api/token/T-1002",   # other patient's appt token
                  "/qr/T-1003", "/api/token/T-1003"):  # other patient's walk-in
        resp = c1.get(other)
        check(f"patient denied {other} (404, no existence oracle)",
              resp.status_code == 404, f"got {resp.status_code}")

    # Doctor: own queue + general walk-in queue only
    c2 = app.test_client()
    login(c2, "doc1", "password1")
    check("doctor gets own-queue QR (200)",
          c2.get("/qr/T-1001").status_code == 200)
    check("doctor gets own-queue status JSON (200)",
          c2.get("/api/token/T-1001").status_code == 200)
    check("doctor denied another doctor's patient token (404)",
          c2.get("/qr/T-1002").status_code == 404
          and c2.get("/api/token/T-1002").status_code == 404)
    check("doctor may view general walk-in queue QR (200)",
          c2.get("/qr/T-1003").status_code == 200)
    check("doctor denied unknown token (404)",
          c2.get("/qr/T-9999").status_code == 404)

    # Receptionist: front-desk view of every queue
    c3 = app.test_client()
    login(c3, "rec1", "password1")
    check("receptionist may view any token QR (200)",
          c3.get("/qr/T-1002").status_code == 200
          and c3.get("/qr/T-1003").status_code == 200)
    resp = c3.get("/api/token/T-1002")
    check("receptionist may view any token JSON (200)",
          resp.status_code == 200)
    check("staff JSON also omits patient details",
          "SECRET" not in resp.get_data(as_text=True))

    restore_env()


# ──────────────────────────────────────────────────────────────────
# M2: CSRF enforcement on state-changing POSTs
# ──────────────────────────────────────────────────────────────────
def test_csrf_enforcement():
    print("\n[M2] CSRF enforcement")
    clear_env()
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"

    class CsrfConfig(TestingConfig):
        WTF_CSRF_ENABLED = True  # production-like CSRF

    app = create_app(CsrfConfig)
    doc_id = make_user(app, "doc1", "doctor")
    pat_id = make_user(app, "pat1", "patient")
    make_user(app, "rec1", "receptionist")
    with app.app_context():
        tok = Token(patient_id=pat_id, doctor_id=doc_id, token_number="T-5001",
                    status="active", queue_position=1)
        db.session.add(tok)
        db.session.commit()
        token_pk = tok.id

    client = app.test_client()

    # Anonymous login attempt without CSRF token -> rejected
    resp = client.post("/login", data={"username": "doc1", "password": "password1"})
    check("POST /login without CSRF token rejected (400)",
          resp.status_code == 400, f"got {resp.status_code}")

    # Real login with CSRF token
    page = client.get("/login").get_data(as_text=True)
    resp = client.post("/login", data={"username": "doc1", "password": "password1",
                                       "csrf_token": csrf_from(page)})
    check("POST /login with valid CSRF token succeeds (302)",
          resp.status_code == 302, f"got {resp.status_code}")

    # Doctor call-next without token -> rejected; state unchanged
    resp = client.post("/doctor/call-next", follow_redirects=False)
    check("POST /doctor/call-next without CSRF token rejected (400)",
          resp.status_code == 400, f"got {resp.status_code}")
    with app.app_context():
        t = db.session.get(Token, token_pk)
        check("token unchanged after rejected call-next",
              t.status == "active", f"got {t.status}")

    # Doctor call-next with valid token from the dashboard form
    page = client.get("/doctor/dashboard").get_data(as_text=True)
    token_value = csrf_from(page)
    check("doctor dashboard renders a CSRF token", bool(token_value))
    resp = client.post("/doctor/call-next", data={"csrf_token": token_value},
                       follow_redirects=False)
    check("POST /doctor/call-next with valid CSRF token works (302)",
          resp.status_code == 302, f"got {resp.status_code}")
    with app.app_context():
        t = db.session.get(Token, token_pk)
        check("token called after valid-token request",
              t.status == "called", f"got {t.status}")

    # Complete without token -> rejected; with token -> works
    resp = client.post(f"/doctor/complete/{token_pk}", follow_redirects=False)
    check("POST /doctor/complete without CSRF token rejected (400)",
          resp.status_code == 400, f"got {resp.status_code}")
    page = client.get("/doctor/dashboard").get_data(as_text=True)
    token_value = csrf_from(page)
    resp = client.post(f"/doctor/complete/{token_pk}",
                       data={"csrf_token": token_value}, follow_redirects=False)
    check("POST /doctor/complete with valid CSRF token works (302)",
          resp.status_code == 302, f"got {resp.status_code}")
    with app.app_context():
        t = db.session.get(Token, token_pk)
        check("token completed after valid-token request",
              t.status == "completed", f"got {t.status}")

    # Receptionist walk-in form: rejected without token, works with one
    rec = app.test_client()
    page = rec.get("/login").get_data(as_text=True)
    rec.post("/login", data={"username": "rec1", "password": "password1",
                             "csrf_token": csrf_from(page)})
    resp = rec.post("/receptionist/walkin",
                    data={"full_name": "Walk In", "phone": "777"},
                    follow_redirects=False)
    check("POST /receptionist/walkin without CSRF token rejected (400)",
          resp.status_code == 400, f"got {resp.status_code}")
    page = rec.get("/receptionist/walkin").get_data(as_text=True)
    token_value = csrf_from(page)
    check("walk-in page renders a CSRF token", bool(token_value))
    resp = rec.post("/receptionist/walkin",
                    data={"full_name": "Walk In", "phone": "777",
                          "csrf_token": token_value},
                    follow_redirects=False)
    check("POST /receptionist/walkin with valid CSRF token works (302)",
          resp.status_code == 302, f"got {resp.status_code}")
    with app.app_context():
        check("walk-in token created after valid-token request",
              Token.query.count() == 2, f"got {Token.query.count()}")

    # GET pages are unaffected by CSRF enforcement
    check("doctor dashboard still renders (GET unaffected)",
          client.get("/doctor/dashboard").status_code == 200)

    restore_env()


def main():
    try:
        test_production_secret_guard()
        test_production_seeding_fail_closed()
        test_token_qr_privacy()
        test_csrf_enforcement()
    finally:
        restore_env()
    print(f"\n{'=' * 60}\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
