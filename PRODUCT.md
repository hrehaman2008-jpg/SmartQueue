---
name: smartqueue-product
description: Smart Queue and Digital Token System for medical clinics.
metadata:
  type: project
---

SmartQueue is a Flask-based application for managing patient queues and digital tokens in a medical clinic setting. It supports three user roles:
- **Patients**: Register, book appointments, and check their status in the queue.
- **Receptionists**: Register walk-in patients and manage the general queue.
- **Doctors**: Manage their specific queues, call patients for consultation, and mark consultations as completed.

Key features:
- Queue Management: Real-time queue positions and token generation.
- Appointment Booking: Patients can book appointments, which are automatically added to the queue.
- QR Code Integration: Each token has a QR code for easy scanning and status checks.
- Role-based Access Control (RBAC): Distinct dashboards and permissions for patients, doctors, and receptionists.
- Security: Flask-Login integration with role-based decorators and CSRF protection.

The system aims to improve clinic efficiency by reducing waiting times and providing patients with a transparent queue management experience.
