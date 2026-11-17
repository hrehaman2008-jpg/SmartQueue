"""Route handlers for the Smart Queue application.

Each route includes:
- Server-side authentication checks (Flask-Login / role decorators)
- Server-side authorization checks (role-based, patient-scoped)
- WTForms server-side validation with error flash messages
"""

import re
import secrets
from datetime import datetime
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
    send_file,
)
from flask_login import current_user, login_user, logout_user, login_required
from sqlalchemy import or_

from . import db, login_manager
from .models import User, Patient, Doctor, Receptionist, Appointment, Token
from .forms import (
    LoginForm,
    RegistrationForm,
    PatientProfileForm,
    BookingForm,
    WalkInRegistrationForm,
    CSRFForm,
)
from .utils import (
    generate_token_number,
    generate_qr_png,
    utc_from_clinic_time,
    get_current_patient,
    get_current_doctor,
    get_current_receptionist,
    role_required,
    recalc_queue_positions,
    get_next_token,
    patient_only,
    doctor_only,
    receptionist_only,
    staff_only,
)

# Blueprint registration
bp = Blueprint("main", __name__)


# ──────────────────────────────────────────────────────────────────
# Public routes
# ──────────────────────────────────────────────────────────────────


@bp.route("/")
def index():
    """Landing page for non-authenticated users."""
    return render_template("index.html")


@bp.route("/about")
def about():
    """About page describing the system.

    Demo credentials are only rendered when the configuration allows
    default demo passwords (non-production); production never publishes
    working credentials.
    """
    return render_template(
        "about.html",
        show_demo_passwords=current_app.config.get(
            "DEMO_SEEDING_ALLOW_DEFAULT_PASSWORDS", True
        ),
    )


# ──────────────────────────────────────────────────────────────────
# Authentication routes
# ──────────────────────────────────────────────────────────────────


@bp.route("/login", methods=["GET", "POST"])
def login():
    """Patient / staff login page."""
    if current_user.is_authenticated:
        flash("You are already logged in.", "info")
        return redirect(url_for("main.index"))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user is None or not user.check_password(form.password.data):
            flash("Invalid username or password.", "error")
            return render_template("auth/login.html", form=form)
        if not user.is_active:
            flash("This account has been deactivated.", "error")
            return render_template("auth/login.html", form=form)

        # Attach the role-specific profile before logging in (profile IDs
        # differ from user IDs, so fetch by relationship not PK).
        attach_user_profile(user)

        login_user(user, remember=form.remember_me.data)
        next_page = request.args.get("next")
        if not next_page or not is_safe_url(next_page):
            next_page = url_for("main.index")
        flash(
            f"Welcome back, {user.patient.full_name if user.patient else user.username}!",
            "success",
        )
        return redirect(next_page)

    return render_template("auth/login.html", form=form)


@bp.route("/register", methods=["GET", "POST"])
def register():
    """Self-service patient registration.

    Only the ``patient`` role can register through this public form.
    Doctors and receptionists are created via the seed script.
    """
    if current_user.is_authenticated:
        flash("You are already logged in.", "info")
        return redirect(url_for("main.index"))

    form = RegistrationForm()
    if form.validate_on_submit():
        # Enforce: only patient accounts can be created here
        if form.data.get("role", "patient") != "patient":
            flash("Registration is only available for patients.", "error")
            return render_template("auth/register.html", form=form)

        if form.full_name.data.upper() == "TOM HOSPITAL" and form.username.data.lower() == "admin":
            # Explicitly reject a hardcoded admin credential pattern
            flash("This credential combination is not allowed.", "error")
            return render_template("auth/register.html", form=form)

        user = User(
            username=form.username.data,
            role="patient",
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()

        patient = Patient(
            user=user,
            full_name=form.full_name.data,
            phone=form.phone.data,
            email=form.email.data,
            date_of_birth=form.date_of_birth.data,
        )
        db.session.add(patient)
        db.session.commit()

        flash("Account created successfully. Please log in.", "success")
        return redirect(url_for("main.login"))

    return render_template("auth/register.html", form=form)


@bp.route("/logout")
@login_required
def logout():
    """Log out the current user."""
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("main.index"))


# ──────────────────────────────────────────────────────────────────
# Patient routes
# ──────────────────────────────────────────────────────────────────


@bp.route("/patient/dashboard")
@patient_only
def patient_dashboard():
    """Patient dashboard: current token, queue position, upcoming appointment."""
    patient = get_current_patient()
    tokens = Token.query.filter_by(patient_id=patient.id).order_by(Token.generated_at.desc()).all()
    # Feature a token that is still live (waiting or being called); only fall
    # back to the most recent token once the visit is over, so the dashboard
    # never presents a finished token as if it were the patient's current one.
    live_token = next((t for t in tokens if t.status in ("active", "called")), None)
    featured_token = live_token or (tokens[0] if tokens else None)
    upcoming_appointments = Appointment.query.filter_by(
        patient_id=patient.id, status="scheduled"
    ).order_by(Appointment.appointment_datetime).all()
    recent_appointments = (
        Appointment.query.filter(
            Appointment.patient_id == patient.id,
            Appointment.status != "scheduled",
        )
        .order_by(Appointment.appointment_datetime.desc())
        .limit(3)
        .all()
    )
    queue_ahead = 0
    if live_token and live_token.queue_position:
        queue_ahead = max(live_token.queue_position - 1, 0)
    return render_template(
        "patient/dashboard.html",
        featured_token=featured_token,
        live_token=live_token,
        upcoming_appointments=upcoming_appointments,
        recent_appointments=recent_appointments,
        all_tokens=tokens[:8],
        queue_ahead=queue_ahead,
    )


@bp.route("/patient/book-appointment", methods=["GET", "POST"])
@patient_only
def book_appointment():
    """Book a new appointment with a doctor."""
    patient = get_current_patient()
    form = BookingForm()
    if form.validate_on_submit():
        appointment_datetime = utc_from_clinic_time(
            datetime.combine(form.appointment_date.data, form.appointment_time.data),
            current_app.config.get("CLINIC_TIMEZONE", "UTC"),
        )
        if appointment_datetime < datetime.utcnow():
            flash("The appointment time must be in the future.", "error")
            return render_template("patient/book_appointment.html", form=form)

        appointment = Appointment(
            patient_id=patient.id,
            doctor_id=form.doctor_id.data,
            appointment_datetime=appointment_datetime,
            purpose=form.purpose.data or "Follow-up consultation",
            status="scheduled",
        )
        db.session.add(appointment)
        db.session.commit()

        # Generate a token for the appointment (scheduled token)
        token = Token(
            patient_id=patient.id,
            doctor_id=form.doctor_id.data,
            appointment_id=appointment.id,
            token_number=generate_token_number(),
            status="active",
        )
        db.session.add(token)
        db.session.flush()
        recalc_queue_positions(form.doctor_id.data)
        db.session.commit()

        doctor = Doctor.query.get(form.doctor_id.data)
        flash(f"Appointment booked with Dr. {doctor.full_name}! Token: {token.token_number}", "success")
        return redirect(url_for("main.patient_dashboard"))

    return render_template("patient/book_appointment.html", form=form)


@bp.route("/patient/history")
@patient_only
def patient_history():
    """Appointment and token history for the patient."""
    patient = get_current_patient()
    appointments = Appointment.query.filter_by(patient_id=patient.id).order_by(
        Appointment.appointment_datetime.desc()
    ).all()
    tokens = Token.query.filter_by(patient_id=patient.id).order_by(
        Token.generated_at.desc()
    ).all()
    return render_template(
        "patient/history.html",
        appointments=appointments,
        tokens=tokens,
    )


@bp.route("/patient/profile", methods=["GET", "POST"])
@patient_only
def patient_profile():
    """Edit patient profile."""
    patient = get_current_patient()
    form = PatientProfileForm()
    if form.validate_on_submit():
        patient.full_name = form.full_name.data
        patient.phone = form.phone.data
        patient.email = form.email.data
        patient.date_of_birth = form.date_of_birth.data
        patient.address = form.address.data
        db.session.commit()
        flash("Profile updated successfully.", "success")
        return redirect(url_for("main.patient_profile"))

    # Pre-fill from existing data
    if request.method == "GET":
        form.full_name.data = patient.full_name
        form.phone.data = patient.phone
        form.email.data = patient.email
        form.date_of_birth.data = patient.date_of_birth
        form.address.data = patient.address or ""

    return render_template("patient/profile.html", form=form)


# ──────────────────────────────────────────────────────────────────
# Receptionist routes
# ──────────────────────────────────────────────────────────────────


@bp.route("/receptionist/dashboard")
@receptionist_only
def receptionist_dashboard():
    """Receptionist dashboard: walk-in registration and live queue."""
    tokens = (
        Token.query.filter_by(status="active", doctor_id=None)
        .order_by(Token.queue_position)
        .all()
    )
    serving_tokens = (
        Token.query.filter_by(status="called", doctor_id=None)
        .order_by(Token.called_at.desc())
        .all()
    )
    recent_tokens = (
        Token.query.filter_by(doctor_id=None)
        .order_by(Token.generated_at.desc())
        .limit(15)
        .all()
    )
    completed_today = (
        Token.query.filter(Token.doctor_id.is_(None), Token.status == "completed")
        .filter(Token.completed_at >= datetime.utcnow().date())
        .count()
    )
    return render_template(
        "receptionist/dashboard.html",
        tokens=tokens,
        serving_tokens=serving_tokens,
        recent_tokens=recent_tokens,
        completed_today=completed_today,
    )


@bp.route("/receptionist/walkin", methods=["GET", "POST"])
@receptionist_only
def walk_in_registration():
    """Walk-in registration: create patient + immediate token."""
    form = WalkInRegistrationForm()
    if form.validate_on_submit():
        phone = form.phone.data.strip()
        try:
            # Reuse an existing patient profile with the same phone number.
            # Match on normalized digits so formatting variants ("555-0142" vs
            # "5550142") resolve to one record instead of colliding on the
            # shared ``walkin_<digits>`` username — ``Patient.user_id`` is
            # unique, so a second Patient for the same User would raise
            # IntegrityError and roll back the whole walk-in.
            patient = Patient.query.filter_by(phone=phone).first()
            if patient is None:
                digits = re.sub(r"\D", "", phone)
                if digits:
                    for cand in Patient.query.filter(Patient.phone.isnot(None)).all():
                        if re.sub(r"\D", "", cand.phone or "") == digits:
                            patient = cand
                            break
            if patient is None:
                # Derive a stable, safe username from the phone number
                username = "walkin_" + re.sub(r"[^0-9A-Za-z]", "", phone)
                user = User.query.filter_by(username=username).first()
                if user is None:
                    user = User(username=username, role="patient")
                    # Random secret: walk-ins never log in, but the account
                    # must not carry a guessable/empty password hash either.
                    user.set_password(secrets.token_hex(24))
                    db.session.add(user)
                    db.session.flush()
                patient = Patient(
                    user_id=user.id,
                    full_name=form.full_name.data,
                    phone=phone,
                    email=form.email.data or None,
                )
                db.session.add(patient)
                db.session.flush()
            elif form.email.data:
                # Keep contact details current without touching anything else
                patient.email = form.email.data

            # Create a walk-in token (no doctor assigned — general queue)
            token = Token(
                patient_id=patient.id,
                doctor_id=None,
                token_number=generate_token_number(),
                status="active",
            )
            db.session.add(token)
            db.session.flush()
            recalc_queue_positions(None)  # all walk-in tokens share the general queue
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Walk-in registration failed")
            flash("Could not register the walk-in patient. Please try again.", "error")
            return render_template("receptionist/walkin_registration.html", form=form)

        flash(f"Walk-in token issued: {token.token_number}. {form.full_name.data} is now in the queue.", "success")
        return redirect(url_for("main.receptionist_dashboard"))

    return render_template("receptionist/walkin_registration.html", form=form)


@bp.route("/receptionist/queue")
@staff_only
def receptionist_queue():
    """Staff queue board: filter by doctor or general queue."""
    doctor_id = request.args.get("doctor_id", type=int)
    if doctor_id:
        scope = Token.doctor_id == doctor_id
        doctor = Doctor.query.get_or_404(doctor_id)
        title = f"Queue — Dr. {doctor.full_name}"
    else:
        scope = Token.doctor_id.is_(None)
        title = "General Queue (walk-ins)"

    # Waiting list stays exactly as before; serving and recent activity are
    # additive read-only views of the same queue scope.
    tokens = (
        Token.query.filter(scope, Token.status == "active")
        .order_by(Token.queue_position)
        .all()
    )
    serving_tokens = (
        Token.query.filter(scope, Token.status == "called")
        .order_by(Token.called_at.desc())
        .all()
    )
    today = datetime.utcnow().date()
    recent_activity = (
        Token.query.filter(scope, Token.status.in_(("completed", "skipped")))
        .filter(Token.completed_at >= today)
        .order_by(Token.completed_at.desc())
        .limit(6)
        .all()
    )
    completed_today = (
        Token.query.filter(scope, Token.status == "completed")
        .filter(Token.completed_at >= today)
        .count()
    )
    skipped_today = (
        Token.query.filter(scope, Token.status == "skipped")
        .filter(Token.completed_at >= today)
        .count()
    )

    doctors = Doctor.query.order_by(Doctor.full_name).all()
    return render_template(
        "receptionist/queue_management.html",
        tokens=tokens,
        serving_tokens=serving_tokens,
        recent_activity=recent_activity,
        completed_today=completed_today,
        skipped_today=skipped_today,
        title=title,
        doctors=doctors,
        active_doctor_id=doctor_id or None,
    )


# ──────────────────────────────────────────────────────────────────
# Doctor routes
# ──────────────────────────────────────────────────────────────────


@bp.route("/doctor/dashboard")
@doctor_only
def doctor_dashboard():
    """Doctor dashboard: call patients, view queue, complete consultations.

    The visible queue is the doctor's own tokens **plus** unassigned
    general walk-in tokens (doctor_id IS NULL), in arrival order.
    """
    doctor = get_current_doctor()
    queue_scope = or_(Token.doctor_id == doctor.id, Token.doctor_id.is_(None))
    active_tokens = (
        Token.query.filter(queue_scope, Token.status == "active")
        .order_by(Token.generated_at, Token.queue_position, Token.id)
        .all()
    )
    next_token = active_tokens[0] if active_tokens else None
    # The token currently being seen (status "called") — hidden from the
    # waiting list on purpose, it needs its own slot on the dashboard.
    # General walk-ins called by any doctor stay visible here: the shared
    # queue has no per-doctor "caller" column (schema unchanged).
    called_token = (
        Token.query.filter(queue_scope, Token.status == "called")
        .order_by(Token.called_at)
        .first()
    )
    # Personal stat only: completions of shared walk-ins are not counted
    # (there is no record of which doctor served a general token).
    completed_today = Token.query.filter(
        Token.doctor_id == doctor.id, Token.status == "completed"
    ).filter(Token.completed_at >= datetime.utcnow().date()).count()
    return render_template(
        "doctor/dashboard.html",
        tokens=active_tokens,
        next_token=next_token,
        called_token=called_token,
        completed_today=completed_today,
        form=CSRFForm(),
    )


@bp.route("/doctor/call-next", methods=["POST"])
@doctor_only
def call_next_token():
    """Call the next patient in this doctor's queue or the general walk-in queue.

    The status flip uses a compare-and-set UPDATE
    (``WHERE id = ? AND status = 'active'``): only one doctor's UPDATE
    can match a given token, so two doctors racing on the same walk-in
    cannot both call it — the loser simply moves to the next candidate.

    Concurrency note: SQLAlchemy's ``with_for_update()`` is a no-op on
    SQLite (no row-level locks), and this project's local/dev database
    is SQLite. The compare-and-set pattern is a single atomic statement
    on both SQLite and PostgreSQL, which is why it is used instead of
    SELECT ... FOR UPDATE; on Postgres it additionally re-evaluates the
    predicate after the competing row lock is released.
    """
    doctor = get_current_doctor()
    for _ in range(5):  # bounded retry: skip tokens claimed mid-flight
        next_token = get_next_token(doctor.id)
        if next_token is None:
            break
        claimed = (
            Token.query.filter(
                Token.id == next_token.id,
                Token.status == "active",
            )
            .update(
                {"status": "called", "called_at": datetime.utcnow()},
                synchronize_session=False,
            )
        )
        db.session.commit()
        if claimed == 1:
            flash(f"Calling token {next_token.token_number}...", "success")
            return redirect(url_for("main.doctor_dashboard"))
        # Lost the race — another doctor claimed it; try the next one.
    flash("No patients waiting.", "info")
    return redirect(url_for("main.doctor_dashboard"))


@bp.route("/doctor/complete/<int:token_id>", methods=["POST"])
@doctor_only
def complete_consultation(token_id):
    """Mark a consultation as completed and free the queue position."""
    doctor = get_current_doctor()
    token = Token.query.filter(
        Token.id == token_id,
        or_(Token.doctor_id == doctor.id, Token.doctor_id.is_(None)),
    ).first_or_404()
    if token.status == "completed":
        flash(f"Token {token.token_number} is already completed.", "info")
        return redirect(url_for("main.doctor_dashboard"))
    was_general = token.doctor_id is None
    token.status = "completed"
    token.completed_at = datetime.utcnow()
    # Keep the linked appointment in step with the token (no appointment for
    # walk-ins). Without this the appointment stays "scheduled" forever and
    # lingers in the patient's Upcoming list after the visit is done.
    if token.appointment is not None:
        token.appointment.status = "completed"
    db.session.commit()

    recalc_queue_positions(None if was_general else doctor.id)
    db.session.commit()
    flash(f"Token {token.token_number} — consultation completed.", "success")
    return redirect(url_for("main.doctor_dashboard"))


@bp.route("/doctor/skip/<int:token_id>", methods=["POST"])
@doctor_only
def skip_token(token_id):
    """Mark a token as skipped (patient not present) and free the position."""
    doctor = get_current_doctor()
    token = Token.query.filter(
        Token.id == token_id,
        or_(Token.doctor_id == doctor.id, Token.doctor_id.is_(None)),
    ).first_or_404()
    if token.status in ("completed", "skipped"):
        flash(f"Token {token.token_number} is already {token.status}.", "info")
        return redirect(url_for("main.doctor_dashboard"))
    was_general = token.doctor_id is None
    token.status = "skipped"
    token.completed_at = datetime.utcnow()
    # A skipped appointment token records a no-show (walk-ins have no
    # appointment, so this is a no-op for them).
    if token.appointment is not None:
        token.appointment.status = "no_show"
    db.session.commit()
    recalc_queue_positions(None if was_general else doctor.id)
    db.session.commit()
    flash(f"Token {token.token_number} marked as skipped.", "info")
    return redirect(url_for("main.doctor_dashboard"))


@bp.route("/doctor/activate/<int:token_id>", methods=["POST"])
@doctor_only
def activate_token(token_id):
    """Return a 'called' token to active queue (patient arrived)."""
    doctor = get_current_doctor()
    token = Token.query.filter(
        Token.id == token_id,
        or_(Token.doctor_id == doctor.id, Token.doctor_id.is_(None)),
    ).first_or_404()
    if token.status != "called":
        # Only a token currently being seen ("called") can return to the
        # queue. Refuse terminal states so a completed/skipped token can
        # never be resurrected into "active".
        flash(
            f"Token {token.token_number} cannot be returned to the queue "
            f"(status: {token.status}).",
            "error",
        )
        return redirect(url_for("main.doctor_dashboard"))
    was_general = token.doctor_id is None
    token.status = "active"
    token.called_at = None
    db.session.commit()
    recalc_queue_positions(None if was_general else doctor.id)
    db.session.commit()
    flash(f"Token {token.token_number} returned to active queue.", "info")
    return redirect(url_for("main.doctor_dashboard"))


@bp.route("/doctor/appointments")
@doctor_only
def doctor_appointments():
    """List of all appointments for this doctor."""
    doctor = get_current_doctor()
    appointments = Appointment.query.filter_by(doctor_id=doctor.id).order_by(
        Appointment.appointment_datetime.desc()
    ).all()
    return render_template("doctor/appointments.html", appointments=appointments)


# ──────────────────────────────────────────────────────────────────
# QR code / token public routes
# ──────────────────────────────────────────────────────────────────


@bp.route("/qr/<token_number>")
@login_required
def qr_code_image(token_number):
    """Serve a token's QR code, rendered in memory.

    Requires login: the image is regenerated from the token number on
    each request so nothing is written to disk — required on serverless
    hosts whose filesystem is read-only (e.g. Vercel) and where ``/tmp``
    is ephemeral across invocations. Viewers are limited to the token's
    own patient and staff (see ``can_view_token``); unauthorized viewers
    receive the same 404 as a nonexistent token so token numbers cannot
    be probed for existence.
    """
    token = Token.query.filter_by(token_number=token_number).first_or_404()
    if not can_view_token(token):
        abort(404)
    return send_file(
        generate_qr_png(token.token_number),
        mimetype="image/png",
        download_name=f"token_{token.token_number}.png",
        max_age=3600,
    )


@bp.route("/api/token/<token_number>")
@login_required
def api_token_info(token_number):
    """Status API for a token — visible only to its patient and to staff.

    Returns only the token number, queue position, status and doctor
    name — never patient details. Unauthorized viewers (and anonymous
    requests, via the login redirect) receive the same treatment as a
    nonexistent token, so token numbers cannot be enumerated.
    """
    token = Token.query.filter_by(token_number=token_number).first()
    if token is None or not can_view_token(token):
        return jsonify({"error": "Token not found"}), 404
    return jsonify({
        "token": token.token_number,
        "position": token.queue_position,
        "status": token.status,
        "doctor": token.doctor.full_name if token.doctor else "General queue",
        "message": STATUS_MESSAGES.get(token.status, "Unknown status"),
    })


# ──────────────────────────────────────────────────────────────────
# Error handlers
# ──────────────────────────────────────────────────────────────────


@bp.errorhandler(403)
def forbidden_error(e):
    flash("You do not have permission to access that page.", "error")
    return render_template("error/403.html"), 403


@bp.errorhandler(404)
def not_found_error(e):
    flash("That page does not exist.", "error")
    return render_template("error/404.html"), 404


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def can_view_token(token):
    """Authorization rule for token-detail routes (``/qr`` and ``/api/token``).

    - receptionists: any token (front-desk view of every queue);
    - doctors: tokens in their own queue plus the general walk-in queue
      (doctor_id is None) — matching what the staff queue board shows;
    - patients: only tokens belonging to their own patient profile.
    """
    if not current_user.is_authenticated:
        return False
    if current_user.role == "receptionist":
        return True
    if current_user.role == "doctor":
        doctor = current_user.doctor
        return doctor is not None and token.doctor_id in (doctor.id, None)
    if current_user.role == "patient":
        patient = current_user.patient
        return patient is not None and token.patient_id == patient.id
    return False


def attach_user_profile(user):
    """Attach role-specific profile to a User instance before login.

    Required because Flask-Login's identity cache is created once and
    role profiles are separate tables — without this, dashboard helpers
    would always return None after the first login.
    """
    if user.is_patient:
        user.patient = Patient.query.filter_by(user_id=user.id).first()
    elif user.is_doctor:
        user.doctor = Doctor.query.filter_by(user_id=user.id).first()
    elif user.is_receptionist:
        user.receptionist = Receptionist.query.filter_by(user_id=user.id).first()
    return user


def is_safe_url(target):
    """Check that a redirect ``next`` URL is local (prevents open redirects).

    ``target`` is resolved against the request host so that relative targets
    (e.g. ``/receptionist/dashboard``) are recognized as same-origin. A bare
    backslash is rejected outright: browsers normalize ``\\`` to ``/``, which
    could otherwise smuggle an off-origin location past the netloc check.
    """
    from urllib.parse import urlparse, urljoin

    if "\\" in target:
        return False
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ("http", "https") and ref_url.netloc == test_url.netloc


STATUS_MESSAGES = {
    "active": "You are next in line.",
    "called": "Your turn is being called. Please proceed to the doctor's room.",
    "completed": "Your consultation has been completed.",
    "skipped": "You were not present for this appointment.",
}
