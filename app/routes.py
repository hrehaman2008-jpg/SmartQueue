"""Route handlers for the Smart Queue application.

Each route includes:
- Server-side authentication checks (Flask-Login / role decorators)
- Server-side authorization checks (role-based, patient-scoped)
- WTForms server-side validation with error flash messages
"""

import os
import math
from datetime import datetime, timedelta
from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
    send_from_directory,
)
from flask_login import current_user, login_user, logout_user, login_required

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
    generate_qr_code,
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
    """About page describing the system."""
    return render_template("about.html")


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
    active_token = tokens[0] if tokens else None
    upcoming_appointments = Appointment.query.filter_by(
        patient_id=patient.id, status="scheduled"
    ).order_by(Appointment.appointment_datetime).all()
    return render_template(
        "patient/dashboard.html",
        active_token=active_token,
        upcoming_appointments=upcoming_appointments,
        all_tokens=tokens,
    )


@bp.route("/patient/book-appointment", methods=["GET", "POST"])
@patient_only
def book_appointment():
    """Book a new appointment with a doctor."""
    patient = get_current_patient()
    form = BookingForm()
    if form.validate_on_submit():
        appointment_datetime = datetime(
            form.appointment_date.data.year,
            form.appointment_date.data.month,
            form.appointment_date.data.day,
            form.appointment_time.data.hour,
            form.appointment_time.data.minute,
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
        recalc_queue_positions(form.doctor_id.data)
        db.session.add(token)
        db.session.commit()
        save_qr_for_token(token)

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
    return render_template("receptionist/dashboard.html")


@bp.route("/receptionist/walkin", methods=["GET", "POST"])
@receptionist_only
def walk_in_registration():
    """Walk-in registration: create patient + immediate token."""
    form = WalkInRegistrationForm()
    if form.validate_on_submit():
        # Look up or create a patient profile
        patient = Patient.query.filter_by(phone=form.phone.data).first()
        if patient is None:
            user = User(username=f"walkin_{form.phone.data}", role="patient")
            user.set_password("")  # walk-ins get no password
            db.session.add(user)
            db.session.commit()
            patient = Patient(user=user, full_name=form.full_name.data, phone=form.phone.data, email=form.email.data or None)
            db.session.add(patient)
            db.session.commit()
        else:
            # Update contact info if changed
            patient.phone = form.phone.data
            patient.email = form.email.data
            db.session.commit()

        # Create a walk-in token (no doctor assigned — general queue)
        token = Token(
            patient_id=patient.id,
            doctor_id=None,
            status="active",
        )
        recalc_queue_positions(None)  # no doctor filter — all walk-in tokens share general queue
        db.session.add(token)
        db.session.commit()
        save_qr_for_token(token)

        flash(f"Walk-in token issued: {token.token_number}. {form.full_name.data} is now in the queue.", "success")
        return redirect(url_for("main.receptionist_dashboard"))

    return render_template("receptionist/walkin_registration.html", form=form)


@bp.route("/receptionist/queue")
@staff_only
def queue_management():
    """Staff queue board: filter by doctor or general queue."""
    doctor_id = request.args.get("doctor_id", type=int)
    if doctor_id:
        tokens = Token.query.filter_by(status="active", doctor_id=doctor_id).order_by(Token.queue_position).all()
        doctor = Doctor.query.get_or_404(doctor_id)
        title = f"Queue — Dr. {doctor.full_name}"
    else:
        tokens = Token.query.filter_by(status="active", doctor_id=None).order_by(Token.queue_position).all()
        title = "General Queue (walk-ins)"

    return render_template("receptionist/queue_management.html", tokens=tokens, title=title)


# ──────────────────────────────────────────────────────────────────
# Doctor routes
# ──────────────────────────────────────────────────────────────────


@bp.route("/doctor/dashboard")
@doctor_only
def doctor_dashboard():
    """Doctor dashboard: call patients, view queue, complete consultations."""
    doctor = get_current_doctor()
    active_tokens = Token.query.filter_by(doctor_id=doctor.id, status="active").order_by(Token.queue_position).all()
    next_token = active_tokens[0] if active_tokens else None
    completed_today = Token.query.filter_by(
        doctor_id=doctor.id, status="completed"
    ).filter(Token.completed_at >= datetime.utcnow().date()).count()
    return render_template(
        "doctor/dashboard.html",
        tokens=active_tokens,
        next_token=next_token,
        completed_today=completed_today,
        form=CSRFForm(),
    )


@bp.route("/doctor/call-next")
@doctor_only
def call_next_token():
    """Call the next patient in the queue."""
    doctor = get_current_doctor()
    next_token = get_next_token(doctor.id)
    if next_token is None:
        flash("No patients waiting.", "info")
        return redirect(url_for("main.doctor_dashboard"))
    next_token.status = "called"
    next_token.called_at = datetime.utcnow()
    db.session.commit()
    flash(f"Calling token {next_token.token_number}...", "success")
    return redirect(url_for("main.doctor_dashboard"))


@bp.route("/doctor/complete/<int:token_id>", methods=["POST"])
@doctor_only
def complete_consultation(token_id):
    """Mark a consultation as completed and free the queue position."""
    doctor = get_current_doctor()
    token = Token.query.filter_by(id=token_id, doctor_id=doctor.id).first_or_404()
    token.status = "completed"
    token.completed_at = datetime.utcnow()
    db.session.commit()
    recalc_queue_positions(doctor.id)
    db.session.commit()
    flash(f"Token {token.token_number} — consultation completed.", "success")
    return redirect(url_for("main.doctor_dashboard"))


@bp.route("/doctor/skip/<int:token_id>", methods=["POST"])
@doctor_only
def skip_token(token_id):
    """Mark a token as skipped (patient not present) and free the position."""
    doctor = get_current_doctor()
    token = Token.query.filter_by(id=token_id, doctor_id=doctor.id).first_or_404()
    token.status = "skipped"
    token.completed_at = datetime.utcnow()
    db.session.commit()
    recalc_queue_positions(doctor.id)
    db.session.commit()
    flash(f"Token {token.token_number} marked as skipped.", "info")
    return redirect(url_for("main.doctor_dashboard"))


@bp.route("/doctor/activate/<int:token_id>", methods=["POST"])
@doctor_only
def activate_token(token_id):
    """Return a 'called' token to active queue (patient arrived)."""
    doctor = get_current_doctor()
    token = Token.query.filter_by(id=token_id, doctor_id=doctor.id).first_or_404()
    token.status = "active"
    token.called_at = None
    db.session.commit()
    recalc_queue_positions(doctor.id)
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


@bp.route("/qrcode/<path:filename>")
def serve_qrcode(filename):
    """Serve a generated QR code PNG."""
    return send_from_directory(
        os.path.join(current_app.root_path, "static", "qrcode"),
        filename,
    )


@bp.route("/api/token/<token_number>")
def api_token_info(token_number):
    """Public API (mobile-friendly): return a token's status.

    Returns only the token number and current status — never patient
    details. Useful for building a patient-facing mobile page.
    """
    token = Token.query.filter_by(token_number=token_number).first()
    if token is None:
        return jsonify({"error": "Token not found"}), 404
    patient = token.patient if token.patient else Patient(full_name="Walk-in patient")
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


def save_qr_for_token(token):
    """Generate a QR code PNG for a token and persist its relative path."""
    filename = generate_qr_code(token.token_number)
    # Store as path relative to instance/ so URLs stay valid regardless of deploy location
    token.qr_code_path = os.path.join("static", "qrcode", filename)
    return filename


def is_safe_url(target):
    """Check that a redirect ``next`` URL is local (prevents open redirects)."""
    from urllib.parse import urlparse
    ref_url = urlparse(request.host_url)
    test_url = urlparse(url_for("main.index") if target.startswith("/") else target)
    return test_url.scheme in ("http", "https") and ref_url.netloc == test_url.netloc


STATUS_MESSAGES = {
    "active": "You are next in line.",
    "called": "Your turn is being called. Please proceed to the doctor's room.",
    "completed": "Your consultation has been completed.",
    "skipped": "You were not present for this appointment.",
}
