"""Database models for the Smart Queue and Digital Token System."""

from datetime import datetime, date
from . import db, login_manager
from flask_login import UserMixin
from .config import BaseConfig


# ──────────────────────────────────────────────────────────────────
# User model (Flask-Login + role handling)
# ──────────────────────────────────────────────────────────────────


class User(UserMixin, db.Model):
    """Base user model — linked to Patient, Doctor, or Receptionist."""

    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="patient")  # patient | doctor | receptionist
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships — one-to-one backwards
    patient = db.relationship("Patient", back_populates="user", uselist=False, cascade="all, delete-orphan")
    doctor = db.relationship("Doctor", back_populates="user", uselist=False, cascade="all, delete-orphan")
    receptionist = db.relationship("Receptionist", back_populates="user", uselist=False, cascade="all, delete-orphan")

    def set_password(self, password):
        """Hash and store the password using Werkzeug."""
        from werkzeug.security import generate_password_hash
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """Verify a password against the stored hash."""
        from werkzeug.security import check_password_hash
        return check_password_hash(self.password_hash, password)

    @property
    def is_patient(self):
        return self.role == "patient" or self.patient is not None

    @property
    def is_doctor(self):
        return self.role == "doctor" or self.doctor is not None

    @property
    def is_receptionist(self):
        return self.role == "receptionist" or self.receptionist is not None


# ──────────────────────────────────────────────────────────────────
# Role‑specific profile tables (one‑to‑one with User)
# ──────────────────────────────────────────────────────────────────


class Patient(db.Model):
    """Patient profile — linked one‑to‑one with User."""

    __tablename__ = "patient"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20))
    email = db.Column(db.String(120))
    date_of_birth = db.Column(db.Date)
    address = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    user = db.relationship("User", back_populates="patient")
    appointments = db.relationship("Appointment", back_populates="patient", cascade="all, delete-orphan")
    tokens = db.relationship("Token", back_populates="patient", cascade="all, delete-orphan")


class Doctor(db.Model):
    """Doctor profile — linked one‑to‑one with User."""

    __tablename__ = "doctor"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    specialization = db.Column(db.String(100))
    qualification = db.Column(db.String(120))
    room_number = db.Column(db.String(10), default="Room A")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    user = db.relationship("User", back_populates="doctor")
    appointments = db.relationship("Appointment", back_populates="doctor", cascade="all, delete-orphan")
    tokens = db.relationship("Token", back_populates="doctor", cascade="all, delete-orphan")


class Receptionist(db.Model):
    """Receptionist profile — linked one‑to‑one with User."""

    __tablename__ = "receptionist"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    hire_date = db.Column(db.Date)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    user = db.relationship("User", back_populates="receptionist")


# ──────────────────────────────────────────────────────────────────
# Appointment model
# ──────────────────────────────────────────────────────────────────


class Appointment(db.Model):
    """Scheduled appointment between a patient and a doctor."""

    __tablename__ = "appointment"

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patient.id"), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey("doctor.id"), nullable=False)
    appointment_datetime = db.Column(db.DateTime, nullable=False)
    purpose = db.Column(db.String(200), default="Follow‑up consultation")
    status = db.Column(
        db.String(20),
        nullable=False,
        default="scheduled",
    )  # scheduled | completed | cancelled | no_show

    # Relationships
    patient = db.relationship("Patient", back_populates="appointments")
    doctor = db.relationship("Doctor", back_populates="appointments")
    tokens = db.relationship("Token", back_populates="appointment")

    # Convenience property
    @property
    def formatted_datetime(self):
        return self.appointment_datetime.strftime("%Y-%m-%d %I:%M %p")


# ──────────────────────────────────────────────────────────────────
# Token model — automatic digital token generation
# ──────────────────────────────────────────────────────────────────


class Token(db.Model):
    """Digital token for queue management.

    Each token has a unique number, is linked to a patient (and optionally
    an appointment), and carries a status that drives the dashboard UI.
    A QR code image is generated locally and stored under
    ``app/static/qrcode/``; the path is recorded here.
    """

    __tablename__ = "token"

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patient.id"), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey("doctor.id"), nullable=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey("appointment.id"), nullable=True)
    token_number = db.Column(db.String(20), nullable=False)  # e.g. "T-0157"
    queue_position = db.Column(db.Integer, nullable=False, default=1)
    status = db.Column(
        db.String(20),
        nullable=False,
        default="active",
    )  # active | called | completed | skipped
    generated_at = db.Column(db.DateTime, default=datetime.utcnow)
    called_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    qr_code_path = db.Column(db.String(200), nullable=True)  # relative to instance folder

    # Relationships
    patient = db.relationship("Patient", back_populates="tokens")
    doctor = db.relationship("Doctor", back_populates="tokens")
    appointment = db.relationship("Appointment", back_populates="tokens")

    # Properties
    @property
    def is_active(self):
        return self.status == "active"

    @property
    def is_called(self):
        return self.status == "called"

    @property
    def is_completed(self):
        return self.status == "completed"

    @property
    def qr_code_url(self):
        """Return a URL relative to the app root for the QR PNG."""
        if self.qr_code_path:
            return f"/static/qrcode/{self.qr_code_path}"
        return None


# ──────────────────────────────────────────────────────────────────
# Flask-Login user loader
# ──────────────────────────────────────────────────────────────────


@login_manager.user_loader
def load_user(user_id):
    """Required by Flask-Login. Returns a User instance or None."""
    return User.query.get(int(user_id))