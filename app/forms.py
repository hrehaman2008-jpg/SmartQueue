"""Form definitions for the Smart Queue application.

All forms include WTForms CSRF protection (enabled in Flask-WTF).
Validation is performed server-side; errors are returned to the user
with the previously entered values so no data is lost on failure.
"""

from datetime import date, datetime
from flask_wtf import FlaskForm
from wtforms import BooleanField
from wtforms import (
    StringField,
    PasswordField,
    EmailField,
    IntegerField,
    DateField,
    TimeField,
    SelectField,
    TextAreaField,
    ValidationError,
)
from wtforms.validators import (
    DataRequired,
    Length,
    EqualTo,
    Email,
    InputRequired,
    Optional,
    Regexp,
)

from app.models import User, Doctor, Patient


# ──────────────────────────────────────────────────────────────────
# Authentication forms
# ──────────────────────────────────────────────────────────────────


class LoginForm(FlaskForm):
    """Login form for patients, doctors, and receptionists."""

    username = StringField("Username", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])
    remember_me = BooleanField("Remember me")


class RegistrationForm(FlaskForm):
    """Patient registration form.

    Only the ``patient`` role may be registered publicly. Creating a
    doctor or receptionist account requires a seed/admin script.
    """

    username = StringField(
        "Username",
        validators=[
            DataRequired(message="Username is required."),
            Length(min=3, max=64, message="Username must be 3-64 characters."),
            Regexp(
                r"^[a-zA-Z0-9_]+$",
                message="Username may only contain letters, numbers, and underscores.",
            ),
        ],
    )
    email = EmailField("Email", validators=[DataRequired(), Email()])
    phone = StringField("Phone Number")
    full_name = StringField("Full Name", validators=[DataRequired()])
    date_of_birth = DateField("Date of Birth", validators=[Optional()])
    password = PasswordField(
        "Password",
        validators=[
            DataRequired(message="Password is required."),
            Length(min=8, message="Password must be at least 8 characters."),
        ],
    )
    confirm_password = PasswordField(
        "Confirm Password",
        validators=[
            DataRequired(message="Please confirm your password."),
            EqualTo("password", message="Passwords must match."),
        ],
    )

    
    def validate_username(self, field):
        if User.query.filter_by(username=field.data).first():
            raise ValidationError("This username is already taken. Choose another.")

    def validate_email(self, field):
        # Check whether this email is already associated with a patient.
        if Patient.query.filter_by(email=field.data).first():
            raise ValidationError("An account with this email already exists.")


# ──────────────────────────────────────────────────────────────────
# Patient profile forms
# ──────────────────────────────────────────────────────────────────


class PatientProfileForm(FlaskForm):
    """Editable patient profile — used by patients to update their details."""

    full_name = StringField("Full Name", validators=[DataRequired()])
    phone = StringField("Phone Number")
    email = EmailField("Email", validators=[Optional(), Email()])
    date_of_birth = DateField("Date of Birth", validators=[Optional()])
    address = TextAreaField("Address", validators=[Optional(), Length(max=200)])


# ──────────────────────────────────────────────────────────────────
# Appointment booking forms
# ──────────────────────────────────────────────────────────────────


class BookingForm(FlaskForm):
    """Patient appointment booking form."""

    doctor_id = SelectField(
        "Doctor",
        coerce=int,
        validators=[InputRequired(message="Please select a doctor.")],
    )
    appointment_date = DateField(
        "Date",
        validators=[InputRequired()],
    )
    appointment_time = TimeField(
        "Time",
        validators=[InputRequired()],
    )
    purpose = TextAreaField(
        "Reason for visit",
        validators=[Optional(), Length(max=200)],
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Populate the doctor dropdown from the database
        self.doctor_id.choices = [
            (d.id, f"{d.full_name} — {d.specialization}")
            for d in Doctor.query.order_by(Doctor.full_name).all()
        ]

    def validate_appointment_date(self, field):
        if field.data < date.today():
            raise ValidationError("Appointment date must be today or later.")


class WalkInRegistrationForm(FlaskForm):
    """Receptionist walk-in registration — creates a token immediately.

    No appointment is required; a patient profile is created (or reused)
    and a digital token is generated on the spot.
    """

    full_name = StringField("Full Name", validators=[DataRequired()])
    phone = StringField("Phone Number", validators=[DataRequired()])
    email = EmailField("Email", validators=[Optional(), Email()])
    reason = TextAreaField("Reason for visit", validators=[Optional(), Length(max=200)])


class CSRFForm(FlaskForm):
    """Simple form for CSRF protection in manual POST actions."""
    pass
