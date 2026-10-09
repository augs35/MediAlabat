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
            "INSERT INTO staff_profiles (user_id, specialization, clinic_id) VALUES (?, ?, ?)",
            (staff_id, "General Medicine", clinic_id),
        )
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
