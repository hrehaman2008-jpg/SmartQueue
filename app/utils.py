"""Utility functions for the Smart Queue application.

Contains helpers for:
- Random token generation (collision-resistant, human-readable)
- QR code generation (local, no external API)
- Role-based access control decorators
- Patient/doctor context helpers
"""

import os
import uuid
import qrcode
from functools import wraps
from flask import (
    abort,
    current_app,
    flash,
    redirect,
    url_for,
)
from flask_login import current_user, login_required
from app.models import Patient, Doctor, Token


# ──────────────────────────────────────────────────────────────────
# Token generation
# ──────────────────────────────────────────────────────────────────


def generate_token_number():
    """Generate a unique, human-readable token string.

    Format: ``T-<4-digit>`` — e.g. ``T-0472``.
    The function retries up to 10 times to avoid the extremely rare
    collision that can occur when two requests hit the same second.
    """
    for _ in range(10):
        # 4 digits gives 10,000 combinations — more than enough for a demo
        candidate = f"T-{uuid.uuid4().int % 10000:04d}"
        if not Token.query.filter_by(token_number=candidate).first():
            return candidate
    # Fallback: use full UUID fragment (extremely unlikely)
    return f"T-{uuid.uuid4().hex[:6].upper()}"


# ──────────────────────────────────────────────────────────────────
# QR code generation (local, uses Pillow)
# ──────────────────────────────────────────────────────────────────


def generate_qr_code(token_number, base_url=None):
    """Generate a QR code PNG for a token and return the relative filename.

    The QR encodes only the token identifier — no patient data,
    no medical information. In production, you would encode a URL
    like ``https://clinic.example/token/T-0472`` that the patient's
    phone can open to show live queue position.

    The PNG is saved under ``app/static/qrcode/`` and the filename
    returned (e.g. ``token_T-0472.png``).
    """
    # Ensure the output directory exists
    static_qr_dir = os.path.join(
        current_app.root_path, "static", "qrcode"
    )
    os.makedirs(static_qr_dir, exist_ok=True)

    # Data payload — only the token identifier
    payload = f"token:{token_number}"

    # Create QR code image
    qr = qrcode.QRCode(
        version=3,  # 29x29 modules — fits ~25 chars comfortably
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=6,
        border=4,
    )
    qr.add_data(payload)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    # Sanitize filename
    safe_name = f"token_{token_number.replace('-', '_')}.png"
    filepath = os.path.join(static_qr_dir, safe_name)
    img.save(filepath, format="PNG")

    return safe_name


# ──────────────────────────────────────────────────────────────────
# Role-based access control
# ──────────────────────────────────────────────────────────────────


def role_required(*allowed_roles):
    """Decorator: restrict a route to specific roles.

    Usage::

        @role_required("doctor", "receptionist")
        def some_route():
            ...

    If the user is not logged in, Flask-Login redirects to the login page.
    If the user is logged in but has a disallowed role, returns 403.
    """

    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapper(*args, **kwargs):
            if current_user.role not in allowed_roles:
                abort(403)
            return view_func(*args, **kwargs)

        return wrapper

    return decorator


# Convenience decorators
patient_only = role_required("patient")
doctor_only = role_required("doctor")
receptionist_only = role_required("receptionist")
staff_only = role_required("doctor", "receptionist")


# ──────────────────────────────────────────────────────────────────
# Context helpers — get the current user's profile
# ──────────────────────────────────────────────────────────────────


def get_current_patient():
    """Return the Patient instance for the logged-in patient, or None."""
    if current_user.is_authenticated and current_user.is_patient:
        return current_user.patient
    return None


def get_current_doctor():
    """Return the Doctor instance for the logged-in doctor, or None."""
    if current_user.is_authenticated and current_user.is_doctor:
        return current_user.doctor
    return None


def get_current_receptionist():
    """Return the Receptionist instance for the logged-in receptionist, or None."""
    if current_user.is_authenticated and current_user.is_receptionist:
        return current_user.receptionist
    return None


# ──────────────────────────────────────────────────────────────────
# Queue position calculation
# ──────────────────────────────────────────────────────────────────


def recalc_queue_positions(doctor_id):
    """Recalculate queue_position for all active tokens of a doctor.

    Tokens are ordered by generated_at (earlier = closer to front).
    Called after a new token is created or one is marked completed/skipped.
    """
    active_tokens = (
        Token.query.filter_by(doctor_id=doctor_id, status="active")
        .order_by(Token.generated_at)
        .all()
    )
    for idx, token in enumerate(active_tokens, start=1):
        token.queue_position = idx
    # Note: caller must commit the session


def get_next_token(doctor_id):
    """Return the active token with queue_position == 1 for a doctor."""
    return (
        Token.query.filter_by(doctor_id=doctor_id, status="active")
        .order_by(Token.queue_position)
        .first()
    )