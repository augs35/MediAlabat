import sqlite3
from datetime import datetime

DB_PATH = "medialabat.db"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL CHECK(role IN ('patient', 'staff')),
    full_name TEXT NOT NULL,     -- kept in sync automatically from the parts below; used everywhere for display
    first_name TEXT,
    middle_name TEXT,
    last_name TEXT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    contact_number TEXT,
    -- Staff sign-ups go unverified -> pending -> active (or rejected). Patients and pre-existing accounts are 'active'.
    status TEXT NOT NULL DEFAULT 'active',
    reviewed_by INTEGER,         -- the staff member who approved / rejected this account
    reviewed_at TEXT,            -- local time of that decision
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS patient_profiles (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    date_of_birth TEXT,
    address TEXT,
    sex TEXT,
    emergency_contact TEXT,              -- legacy single-field value, kept for old accounts
    emergency_contact_name TEXT,
    emergency_contact_relation TEXT,
    emergency_contact_number TEXT,
    existing_conditions TEXT,
    allergies TEXT,
    medications TEXT,
    medical_history TEXT
);

CREATE TABLE IF NOT EXISTS staff_profiles (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    specialization TEXT,
    clinic_id INTEGER REFERENCES clinics(id)
);

CREATE TABLE IF NOT EXISTS clinics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    address TEXT
);

-- Weekly recurring availability set by staff, e.g. Monday 09:00-12:00
CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    staff_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    weekday INTEGER NOT NULL,        -- 0=Monday .. 6=Sunday
    start_time TEXT NOT NULL,        -- "09:00"
    end_time TEXT NOT NULL,          -- "12:00"
    slot_minutes INTEGER DEFAULT 30
);

-- A doctor/staff member can work at several clinics. (staff_profiles.clinic_id is the old single-clinic column; no longer written.)
CREATE TABLE IF NOT EXISTS staff_clinics (
    staff_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    clinic_id INTEGER NOT NULL REFERENCES clinics(id),
    PRIMARY KEY (staff_id, clinic_id)
);

-- One-time passcodes (staff sign-up and staff log-in). Only a salted hash of the code is stored.
CREATE TABLE IF NOT EXISTS otp_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purpose TEXT NOT NULL CHECK(purpose IN ('signup','login')),
    salt TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    used INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),       -- UTC
    expires_at TEXT NOT NULL                          -- UTC
);

-- In-app notifications for patients (shown as toasts): their appointment was approved / needs rescheduling.
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,                               -- 'approved' | 'reschedule'
    appointment_id INTEGER REFERENCES appointments(id) ON DELETE CASCADE,
    seen INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Outgoing SMS queue. The ESP32 + SIM800L gateway polls this (see sms_gateway.py / docs/ESP32_SMS_GATEWAY.md).
CREATE TABLE IF NOT EXISTS sms_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    to_number TEXT NOT NULL,                 -- +639XXXXXXXXX
    message TEXT NOT NULL,                   -- already cleaned: ASCII, one line, <= 160 chars
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sending','sent','failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,   -- UTC
    claimed_at TEXT,                             -- UTC; when a gateway last took it
    sent_at TEXT                                 -- UTC
);

-- Single row: when the gateway last called in, and what it reported (e.g. signal strength).
CREATE TABLE IF NOT EXISTS sms_gateway (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    last_seen TEXT,                              -- UTC
    rssi INTEGER
);

CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    staff_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    clinic_id INTEGER NOT NULL REFERENCES clinics(id),
    appt_date TEXT NOT NULL,   -- "2026-08-18"
    appt_time TEXT NOT NULL,   -- "09:00"
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'Pending' CHECK(status IN ('Pending','Approved','Reschedule')),
    ai_note TEXT,               -- short AI guidance generated from the reason, shown to the patient
    decided_at TEXT,            -- local time staff approved / asked to reschedule; drives the History page
    ai_chat_closed INTEGER NOT NULL DEFAULT 0,   -- 1 = patient ended the AI conversation (the appointment itself is untouched)
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- A specific date+time a staff member has manually closed off (e.g. truly fully booked,
-- on leave that day, etc). Separate from the weekly recurring `schedules` pattern —
-- this blocks one exact slot instead of a whole recurring block.
CREATE TABLE IF NOT EXISTS blocked_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    staff_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    slot_date TEXT NOT NULL,
    slot_time TEXT NOT NULL,
    UNIQUE(staff_id, slot_date, slot_time)
);

-- Follow-up messages a patient exchanges with the AI about a specific booked
-- appointment, shown as a continuing chat thread on the My Appointments page.
CREATE TABLE IF NOT EXISTS appointment_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appointment_id INTEGER NOT NULL REFERENCES appointments(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('patient', 'ai')),
    content TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def _ensure_column(conn, table, column, coltype):
    """Add a column if it doesn't exist yet — lets older databases upgrade in place."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
        conn.commit()


def _migrate_rejected_to_reschedule(conn):
    """
    Older databases have a CHECK(status IN (..., 'Rejected')) baked into the
    appointments table itself — SQLite can't ALTER a CHECK constraint in place,
    so this rebuilds the table with the new constraint and renames any existing
    'Rejected' rows to 'Reschedule' in the same pass. No-op on a fresh database,
    since SCHEMA above already creates the table with the new constraint.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='appointments'"
    ).fetchone()
    if not row or "Reschedule" in row["sql"]:
        return  # table doesn't exist yet, or already migrated

    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("ALTER TABLE appointments RENAME TO appointments_old")
    conn.execute("""
        CREATE TABLE appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            staff_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            clinic_id INTEGER NOT NULL REFERENCES clinics(id),
            appt_date TEXT NOT NULL,
            appt_time TEXT NOT NULL,
            reason TEXT,
            status TEXT NOT NULL DEFAULT 'Pending' CHECK(status IN ('Pending','Approved','Reschedule')),
            ai_note TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        INSERT INTO appointments (id, patient_id, staff_id, clinic_id, appt_date, appt_time, reason, status, ai_note, created_at)
        SELECT id, patient_id, staff_id, clinic_id, appt_date, appt_time, reason,
               CASE WHEN status = 'Rejected' THEN 'Reschedule' ELSE status END,
               ai_note, created_at
        FROM appointments_old
    """)
    conn.execute("DROP TABLE appointments_old")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    conn.commit()

    # Migrations, in order: ai_note column first (so the Rejected->Reschedule
    # rebuild below can safely copy it), then the status-value rename itself.
    _ensure_column(conn, "appointments", "ai_note", "TEXT")
    _migrate_rejected_to_reschedule(conn)
    # After the rebuild above (which recreates the table from a fixed column list), so it is never lost.
    _ensure_column(conn, "appointments", "decided_at", "TEXT")
    _ensure_column(conn, "appointments", "ai_chat_closed", "INTEGER NOT NULL DEFAULT 0")

    # Staff approval: every account that exists before this change stays usable ('active').
    _ensure_column(conn, "users", "status", "TEXT NOT NULL DEFAULT 'active'")
    _ensure_column(conn, "users", "reviewed_by", "INTEGER")
    _ensure_column(conn, "users", "reviewed_at", "TEXT")

    # Staff -> several clinics: move the old single clinic into staff_clinics exactly once (then clear it,
    # otherwise a clinic the staff member later removes would be resurrected from the old column on every start).
    conn.execute(
        "INSERT OR IGNORE INTO staff_clinics (staff_id, clinic_id) "
        "SELECT user_id, clinic_id FROM staff_profiles WHERE clinic_id IS NOT NULL"
    )
    conn.execute("UPDATE staff_profiles SET clinic_id = NULL WHERE clinic_id IS NOT NULL")
    conn.commit()

    # Migration: older databases created before names/emergency contact were split
    _ensure_column(conn, "users", "first_name", "TEXT")
    _ensure_column(conn, "users", "middle_name", "TEXT")
    _ensure_column(conn, "users", "last_name", "TEXT")
    _ensure_column(conn, "patient_profiles", "emergency_contact_name", "TEXT")
    _ensure_column(conn, "patient_profiles", "emergency_contact_relation", "TEXT")
    _ensure_column(conn, "patient_profiles", "emergency_contact_number", "TEXT")

    # Seed clinics + a demo doctor account if empty, so the app is usable immediately
    if conn.execute("SELECT COUNT(*) c FROM clinics").fetchone()["c"] == 0:
        clinics = [
            ("Alabat Rural Health Unit", "Alabat, Quezon"),
            ("ABC Health Clinic", "Downtown District"),
            ("Wellness Community Clinic", "Riverside Ave"),
        ]
        conn.executemany("INSERT INTO clinics (name, address) VALUES (?, ?)", clinics)
        conn.commit()

    if conn.execute("SELECT COUNT(*) c FROM users WHERE role='staff'").fetchone()["c"] == 0:
        from werkzeug.security import generate_password_hash

        cur = conn.execute(
            "INSERT INTO users (role, full_name, email, password_hash, contact_number) "
            "VALUES ('staff', ?, ?, ?, ?)",
            ("Dr. Juan Santos", "doctor@medialabat.test", generate_password_hash("password123"), "0917-000-0000"),
        )
        staff_id = cur.lastrowid
        clinic_id = conn.execute("SELECT id FROM clinics LIMIT 1").fetchone()["id"]
        conn.execute(
            "INSERT INTO staff_profiles (user_id, specialization) VALUES (?, ?)",
            (staff_id, "General Medicine"),
        )
        conn.execute("INSERT INTO staff_clinics (staff_id, clinic_id) VALUES (?, ?)", (staff_id, clinic_id))
        # Default weekly schedule: Mon/Tue/Thu 9-12 and 1-4
        for wd in (0, 1, 3):
            conn.execute(
                "INSERT INTO schedules (staff_id, weekday, start_time, end_time) VALUES (?,?,?,?)",
                (staff_id, wd, "09:00", "12:00"),
            )
        for wd in (0, 3):
            conn.execute(
                "INSERT INTO schedules (staff_id, weekday, start_time, end_time) VALUES (?,?,?,?)",
                (staff_id, wd, "13:00", "16:00"),
            )
        conn.commit()

    conn.close()
