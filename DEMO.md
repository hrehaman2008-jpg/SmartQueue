# SmartQueue — College Demo Checklist

## Start the application

```bash
# from the project root (C:\Users\ASUS\Documents\SmartQueue)
venv\Scripts\python run.py
```

Then open: **http://127.0.0.1:5000**

Demo accounts (see `README.md` / `app/config.py` for the credentials — not repeated here):
`patient_demo`, `reception_demo`, `doctor_demo`.

> Use a private/incognito window between roles so sessions don't overlap, or log out
> from the user menu (top-right) before switching roles.

---

## The demo flow (7 steps)

1. **Landing page** — open the URL above. Show the hero, the six feature cards, and
   the "How It Works" steps.

2. **Patient: book an appointment**
   - Log in as `patient_demo` → **Book Appointment**.
   - Pick a doctor (e.g. Dr. Demo), choose a future date + time, add a purpose.
   - Submit → redirect to the Patient Dashboard showing the new token and the
     appointment under **Upcoming Appointments**.

3. **Receptionist: register a walk-in**
   - Log out → log in as `reception_demo`.
   - **Register Walk-In Patient** → enter name + phone (a real-looking number) →
     **Issue Walk-In Token**.
   - Confirm the token appears on the dashboard / **Manage Queue** board and the
     "Patients Currently Waiting" count increases.

4. **Doctor: call the next eligible patient**
   - Log out → log in as `doctor_demo`.
   - On the Doctor Dashboard, the booked appointment should be in the **Waiting Queue**.
   - Press **Call Next** → the patient moves into **Currently Serving**.

5. **Show the currently serving token**
   - The large token number, patient name, and "Called" badge are displayed in the
     featured panel.

6. **Complete or skip the consultation**
   - **Complete** → token becomes *Completed*, queue advances, and (for the booked
     appointment) its status updates from *Scheduled* → *Completed*.
   - Or **Skip** → token becomes *Skipped*; a booked appointment becomes *No show*.

7. **Show the resulting queue and appointment status**
   - Doctor Dashboard: Waiting Queue now excludes the ended token; **Completed** count
     increments.
   - Patient side: log back in as `patient_demo` → **Appointment History** shows the
     updated badge (Completed / No show), and the dashboard's Upcoming list no longer
     lists a completed visit.

---

## Quick sanity checks during the demo

- Queue positions rebalance with no gaps after complete/skip.
- Receptionist **Manage Queue** board hides completed/skipped tokens.
- Status badges are colour-coded (Waiting=teal, Called=amber, Completed=green,
  No show/Skipped=red/grey).
- No browser console errors; pages are responsive down to phone width.
