# MediAlabat — Clinic Appointment System

A minimal patient/staff appointment booking system built with Flask and plain
SQLite (no ORM/extra frameworks needed — just `pip install flask`).

## Appointment statuses: Pending / Approved / Reschedule

There's no "Rejected" status — declining a request is framed as asking the
patient to reschedule, not turning them away. This is the actual stored
value in the database (not just a display label), so `db.py` includes a
one-time migration that rewrites any existing `'Rejected'` rows to
`'Reschedule'` the first time the app runs against an older database.

## Slot availability: staff decide, not the system

A patient requesting a time slot no longer auto-blocks it for other patients —
multiple patients can request the same slot while it's Pending. A slot only
becomes unavailable when staff explicitly mark it "Fully Booked" from the
**Manage Specific Dates** calendar on `/staff/schedule` (or when staff
approve a request for it). Staff can reopen a blocked slot anytime.

## Doctor/Staff sign up

Anyone can create a staff account at `/staff/signup` (linked from both the
login and patient sign-up pages) — no admin approval step exists yet, so
treat this as a demo-stage flow if you deploy it for real use.

## Language: English / Filipino (Tagalog)

Every page supports both languages — click **EN / TL** in the navbar (or the
floating switcher on the login/signup pages). The choice is remembered for
the session. All translation strings live in one place: `translations.py`.

The Health Assistant AI also replies in whichever language is active, and
its health-topic filter recognizes common Tagalog symptom words (sakit,
lagnat, ubo, hilo, suka, etc.) alongside English ones — patients can describe
how they feel in either language.

To add more languages later, add a new language code to `SUPPORTED_LANGS`
in `translations.py` and fill in a third value for each key.

## Setup (in VS Code)

1. Open this folder in VS Code.
2. Open a terminal (`` Ctrl+` ``) and create a virtual environment:
   ```bash
   python -m venv venv
   # Windows:
   venv\Scripts\activate
   # Mac/Linux:
   source venv/bin/activate
   ```
3. Install the only dependency:
   ```bash
   pip install -r requirements.txt
   ```
4. Run the app:
   ```bash
   python app.py
   ```
5. Open **http://127.0.0.1:5000** in your browser.

The database (`medialabat.db`) and seed data (3 demo clinics + 1 demo doctor)
are created automatically the first time you run the app.

## Demo accounts

- **Staff/Doctor login:** `doctor@medialabat.test` / `password123`
- **Patient:** sign up your own via the Sign Up page.

## Pages included

**Patient side**
- Sign Up (full name, email, password, contact, DOB, address, sex, emergency contact)
- Login
- Patient Profile (personal info + health info: conditions, allergies, medications, history)
- Patient Dashboard (upcoming appointment card + quick actions)
- Health Assistant (chat about a symptom/concern, gets AI guidance — see below)
- Set Appointment wizard: Select Clinic → Select Date/Time → Reason → Confirm & Submit
- My Appointments (Pending / Approved / Rejected, texted when staff decide)

**Staff/doctor side**
- Staff Dashboard (pending count + today's appointments)
- Appointment Requests list
- Appointment/Application Detail (patient + health info, Approve/Reject)
- Schedule Management (weekly recurring availability blocks — this is what
  drives which dates/times patients can pick)
- Staff Profile (name, specialization, contact, clinic)

## How slot generation works

Staff set **recurring weekly availability** (e.g. "Monday 9:00–12:00") on the
Schedule page. When a patient books, `slots.py` expands that into 30-minute
slots for the next 14 days, and automatically hides any slot that's already
booked (or in the past, for today). No manual slot creation needed.

## SMS notifications on approve/reject

When a staff member approves or rejects an appointment, the patient's
contact number gets a text via **Semaphore** (https://semaphore.co) — an SMS
API built specifically for Philippine mobile numbers. Pay-as-you-go, no
monthly minimum. If it isn't configured, the app just prints the message to
the terminal instead of failing, so you can develop without an account.

1. Create a free account at https://semaphore.co and grab your **API Key**.
2. Top up some credits (roughly ₱0.50/SMS, no subscription).
3. Set it as an environment variable before running the app:
   ```bash
   export SEMAPHORE_API_KEY="your_api_key_here"     # Mac/Linux
   $env:SEMAPHORE_API_KEY="your_api_key_here"        # Windows PowerShell
   ```

Numbers typed in any common format (`0917-111-2222`, `+639171112222`, etc.)
are normalized automatically — see `normalize_ph_number()` in `sms.py`.

## AI note on booked appointments

Submitting an appointment is instant — no AI call happens during submit.
Instead, the next time the patient opens **My Appointments**, their "Reason
for Visit" is passed through the same health-only check and, if relevant,
the AI generates a short (1-2 sentence) note. It's shown as a little chat
exchange right under that appointment — their reason as one bubble, the
AI's note as the reply — the same visual style as the Health Assistant page.
The note is generated once and saved, so it won't regenerate on later visits.
If the reason doesn't read as health-related, or Ollama isn't running, no
note is shown at all — booking itself never fails or blocks because of this.

### The AI mirrors whichever language the patient actually typed in

Both the Health Assistant chat and the appointment note detect the language
of the patient's own message (looking for common Tagalog marker words) and
reply in that language — independent of the site's EN/TL toggle. A patient
can leave the site set to English and still type their concern in Tagalog
and get a Tagalog reply, or vice versa. Short or ambiguous messages fall
back to whatever the site's current language is set to.

## Health Assistant (local AI, via Ollama)

Patients can describe a symptom or health concern on the **Health Assistant**
page and get back short, careful guidance — powered by a **local** AI model
through [Ollama](https://ollama.com), so no API key or per-message cost.
It only responds to health-related messages; anything else gets:

> "Sorry, that's unrelated to a health concern. Please try again."

It never diagnoses, never gives exact medication dosages, and tells the
patient to seek in-person/emergency care for anything that sounds serious or
urgent — always nudging them back toward booking an actual appointment.

### Set it up

1. Install Ollama: https://ollama.com/download
2. Pull the model (one-time):
   ```bash
   ollama pull llama3.2
   ```
3. Start Ollama and leave it running:
   ```bash
   ollama serve
   ```
4. In a separate terminal, run MediAlabat as usual:
   ```bash
   python app.py
   ```

That's it — `health_ai.py` talks to Ollama at `http://localhost:11434`,
the same way your original reference project did. If Ollama isn't running,
the page shows a friendly "assistant isn't reachable right now" message
instead of crashing.

Want a different model? Change `OLLAMA_MODEL` at the top of `health_ai.py`
to whatever you've pulled (`ollama list` shows what's installed).

## Project structure

```
medialabat/
├── app.py              # routes
├── db.py                # SQLite schema + seed data
├── slots.py             # availability/slot-generation logic
├── sms.py                # Semaphore SMS notifications
├── health_ai.py          # Ollama-backed health assistant (patient-facing)
├── requirements.txt
├── templates/
│   ├── base.html
│   ├── auth/            # signup, login
│   ├── patient/         # dashboard, profile, appointment wizard, my appointments, health assistant
│   └── staff/            # dashboard, requests, request detail, schedule, profile
└── static/style.css
```

## Notes / next steps

This is intentionally minimal so it's easy to read and extend. Natural next
additions: multiple doctors per clinic (currently one doctor is auto-assigned
per clinic), email/SMS reminders, password reset, and admin account creation
for staff (right now the first staff account is seeded directly in `db.py`).

## Dark mode

A 🌙/☀️ button sits at the top right of every page (inside the navbar when
logged in, floating on the login/sign-up pages). The choice is saved in the
browser (`localStorage`), so it sticks across pages and visits.

## Names and emergency contacts

Sign-up collects **First / Middle (optional) / Last** name separately; the
combined `full_name` is generated automatically and used for display
everywhere else. Patients' emergency contact is likewise stored as **name,
relation, and number**. Accounts created before this change keep working —
their old single-field emergency contact is still shown until they edit it.

## Clinic management

Doctors/staff can add, edit, and delete clinics from the **Clinics** page.
Patients see changes immediately in the booking flow. A clinic that still has
staff assigned or appointments booked can't be deleted (to avoid orphaning
that data) — reassign or clear those first.

## Appointment history (doctors/staff)

**Requests → History** (button at the top of the Requests page, or `/staff/history`) lists every
appointment you've already decided: **Approved** or **Reschedule**, newest decision first, with filter tabs
(All / Approved / Reschedule) and the date/time you decided. "View Details" opens the full application.
Each staff member only sees their own history. The decision time is stored in `appointments.decided_at`
(older appointments decided before this existed simply show no "Decided on" line). Deciding an
appointment twice isn't possible — a repeated request is ignored so the original decision stands.

## Schedule page

- **Manage Specific Dates** shows one month at a time with ‹ › arrows to move between months (you can't go
  back before the current month, and can look ahead about a year). Month and date names follow the EN/TL toggle.
- The left panel (weekly schedule + Add Schedule Block) and the right panel (calendar + slots) each scroll
  on their own and are sized to fit the window, so the page itself doesn't need to scroll.

## Notes for editing the styles

Every color that differs between light and dark now comes from a CSS variable defined in `:root` and
`:root[data-theme="dark"]` at the top of `static/style.css` (status chips, AM/PM time buttons, calendar tints,
buttons, navbar, etc.). When adding a new component, use those variables rather than hard-coded
colors like `white` or `#ecfdf9`, otherwise it will look wrong in one of the two themes.

## Notes for editing the health keyword list (`health_ai.py`)

The list in `_HEALTH_HINTS` is one big `a|b|c` pattern. Don't leave a `|` right before the closing `)`:
an empty alternative matches *everything*, which silently turns the health-only filter off. The pattern also
accepts a trailing `s`, `es`, `ed` or `ing`, so list the base word only ("headache" covers "headaches").
