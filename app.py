import functools
import hmac
import os
import secrets
import sqlite3
import time
from datetime import datetime, date, timedelta

from flask import (
    Flask, render_template, request, redirect, url_for, session, flash, abort, Response, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash

from db import get_db, init_db
from slots import (
    daterange_next_days, generate_slots_for_day, any_availability,
    time_options, build_calendar_months, slot_statuses_for_day, weekdays_with_schedule,
    build_month, shift_month,
)
from sms import send_sms, appointment_status_message, sms_mode
import sms_gateway
import otp
import notify
from sms_gateway import to_e164_ph
from health_ai import get_health_response, get_appointment_note
from translations import t as _t, SUPPORTED_LANGS, DEFAULT_LANG

def _load_secret_key():
    """
    The key that signs login cookies. It used to be a constant written in the source, which meant anyone who read the code
    could forge a cookie for any account and walk straight past the new passcode check. Now: SECRET_KEY if set,
    otherwise a random key generated once and kept in .secret_key next to this file (don't share or commit it).
    """
    key = os.environ.get("SECRET_KEY", "").strip()
    if key:
        return key
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".secret_key")
    try:
        with open(path) as fh:
            key = fh.read().strip()
        if key:
            return key
    except FileNotFoundError:
        pass
    except OSError:
        return secrets.token_hex(32)           # unreadable file: fall back to a per-run key (everyone logs in again after a restart)
    key = secrets.token_hex(32)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(key)
    except OSError:
        pass
    return key


app = Flask(__name__)
app.secret_key = _load_secret_key()


# ---------- Auth helpers ----------

def current_user():
    if "user_id" not in session:
        return None
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    conn.close()
    return user


def current_lang():
    return session.get("lang", DEFAULT_LANG)


def patient_health_context(conn, user_id):
    """Short string of the patient's known conditions/allergies/medications, for AI context."""
    profile = conn.execute(
        "SELECT existing_conditions, allergies, medications FROM patient_profiles WHERE user_id=?",
        (user_id,),
    ).fetchone()
    parts = []
    if profile:
        if profile["existing_conditions"]:
            parts.append(f"existing conditions: {profile['existing_conditions']}")
        if profile["allergies"]:
            parts.append(f"allergies: {profile['allergies']}")
        if profile["medications"]:
            parts.append(f"current medications: {profile['medications']}")
    return "; ".join(parts) if parts else None


@app.context_processor
def inject_globals():
    lang = current_lang()
    def fdate(d):
        """'October 8, 2026' / 'Oktubre 8, 2026' — month name follows the active language."""
        return f"{_t('month.' + str(d.month), lang=lang)} {d.day}, {d.year}"

    def fdatetime(value):
        """Format a stored 'YYYY-MM-DD HH:MM' string; returns '' for empty/unparseable values."""
        try:
            dt = datetime.strptime(value, "%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            return ""
        hour12 = dt.hour % 12 or 12
        return f"{fdate(dt.date())} · {hour12}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"

    return {
        "current_user": current_user(),
        "current_lang": lang,
        "t": lambda key, **kwargs: _t(key, lang=lang, **kwargs),
        "fdate": fdate,
        "fdatetime": fdatetime,
    }


@app.route("/lang/<code>")
def set_language(code):
    if code in SUPPORTED_LANGS:
        session["lang"] = code
    return redirect(request.referrer or url_for("home"))


def login_required(role=None):
    def decorator(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if user is None:
                return redirect(url_for("login"))
            if user["role"] == "staff" and user["status"] != "active":
                session.clear()                    # an unapproved / rejected staff account must never get in
                return redirect(url_for("login"))
            if role and user["role"] != role:
                abort(403)
            return view(user, *args, **kwargs)
        return wrapped
    return decorator


def join_name(first, middle, last):
    """Combine First/Middle/Last into one display name, skipping blanks."""
    parts = [p.strip() for p in (first, middle, last) if p and p.strip()]
    return " ".join(parts)


# ---------- Public / Auth routes ----------

@app.route("/")
def home():
    if current_user():
        u = current_user()
        return redirect(url_for("staff_dashboard") if u["role"] == "staff" else url_for("patient_dashboard"))
    return redirect(url_for("login"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        f = request.form
        conn = get_db()
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (f["email"],)).fetchone()
        if existing:
            flash(_t("auth.email_exists", lang=current_lang()), "error")
            conn.close()
            return render_template("auth/signup.html", form=f)

        full_name = join_name(f["first_name"], f.get("middle_name", ""), f["last_name"])
        cur = conn.execute(
            "INSERT INTO users (role, full_name, first_name, middle_name, last_name, email, password_hash, contact_number) "
            "VALUES ('patient', ?, ?, ?, ?, ?, ?, ?)",
            (full_name, f["first_name"], f.get("middle_name", ""), f["last_name"],
             f["email"], generate_password_hash(f["password"]), f["contact_number"]),
        )
        user_id = cur.lastrowid
        conn.execute(
            "INSERT INTO patient_profiles "
            "(user_id, date_of_birth, address, sex, emergency_contact_name, emergency_contact_relation, emergency_contact_number) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, f["date_of_birth"], f["address"], f["sex"],
             f["emergency_contact_name"], f["emergency_contact_relation"], f["emergency_contact_number"]),
        )
        conn.commit()
        conn.close()
        flash(_t("auth.signup_success", lang=current_lang()), "success")
        return redirect(url_for("login"))

    return render_template("auth/signup.html", form={})


@app.route("/staff/signup", methods=["GET", "POST"])
def staff_signup():
    conn = get_db()
    clinics = conn.execute("SELECT * FROM clinics ORDER BY name").fetchall()
    if request.method == "POST":
        f = request.form
        lang = current_lang()
        error = None
        if not to_e164_ph(f.get("contact_number")):
            error = "auth.staff_phone_required"          # codes are texted here, so it has to be a real PH mobile number
        elif conn.execute("SELECT id FROM users WHERE email = ?", (f["email"],)).fetchone():
            error = "auth.email_exists"
        if error:
            flash(_t(error, lang=lang), "error")
            conn.close()
            return render_template("auth/staff_signup.html", form=f, clinics=clinics,
                                   chosen_clinics={int(x) for x in f.getlist("clinic_ids") if x.isdigit()})

        full_name = join_name(f["first_name"], f.get("middle_name", ""), f["last_name"])
        initial_status = "unverified" if otp.otp_required() else "pending"
        cur = conn.execute(
            "INSERT INTO users (role, full_name, first_name, middle_name, last_name, email, password_hash, contact_number, status) "
            "VALUES ('staff', ?, ?, ?, ?, ?, ?, ?, ?)",
            (full_name, f["first_name"], f.get("middle_name", ""), f["last_name"],
             f["email"], generate_password_hash(f["password"]), f["contact_number"], initial_status),
        )
        user_id = cur.lastrowid
        conn.execute("INSERT INTO staff_profiles (user_id, specialization) VALUES (?, ?)", (user_id, f.get("specialization", "")))
        valid_ids = {c["id"] for c in clinics}
        for cid in {int(x) for x in f.getlist("clinic_ids") if x.isdigit()} & valid_ids:
            conn.execute("INSERT INTO staff_clinics (staff_id, clinic_id) VALUES (?, ?)", (user_id, cid))
        conn.commit()

        if initial_status == "unverified":
            user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            flash(_t("auth.verify_to_continue", lang=lang), "info")
            return _start_otp(conn, user, "signup")       # closes conn
        conn.close()
        flash(_t("auth.staff_pending", lang=lang), "info")
        return redirect(url_for("login"))

    conn.close()
    return render_template("auth/staff_signup.html", form={}, clinics=clinics, chosen_clinics=set())


OTP_SESSION_SECONDS = 15 * 60


def _fresh_session(**values):
    """Start a clean login session (drops any old identity) but keeps the language choice and pending flash messages."""
    keep = {k: session[k] for k in ("lang", "_flashes") if k in session}
    session.clear()
    session.update(keep)
    session.update(values)


def _start_otp(conn, user, purpose):
    """Text a code to this staff member and send them to the code page. Closes `conn`."""
    status, _wait = otp.issue(conn, user["id"], purpose, user["contact_number"])
    conn.close()
    _fresh_session(otp_pending={"user_id": user["id"], "purpose": purpose, "at": int(time.time())})
    lang = current_lang()
    if status == "send_failed":
        flash(_t("otp.send_failed", lang=lang), "error")
    elif status == "too_many":
        flash(_t("otp.too_many", lang=lang), "error")
    return redirect(url_for("otp_page"))


def _begin_staff_login(conn, user):
    """Password was right. Staff still have to be approved and pass the passcode check. Closes `conn`."""
    lang = current_lang()
    status = user["status"]
    if status == "rejected":
        conn.close()
        flash(_t("auth.staff_rejected", lang=lang), "error")
        return render_template("auth/login.html")
    if status == "pending":
        conn.close()
        flash(_t("auth.staff_pending", lang=lang), "info")
        return render_template("auth/login.html")
    if status == "unverified":                       # never finished the phone check at sign-up: pick it up again
        if not otp.otp_required():
            conn.execute("UPDATE users SET status='pending' WHERE id=?", (user["id"],))
            conn.commit()
            conn.close()
            flash(_t("auth.staff_pending", lang=lang), "info")
            return render_template("auth/login.html")
        return _start_otp(conn, user, "signup")
    if not otp.otp_required():                       # STAFF_OTP_REQUIRED=0
        conn.close()
        _fresh_session(user_id=user["id"])
        return redirect(url_for("staff_dashboard"))
    return _start_otp(conn, user, "login")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email = ?", (request.form["email"],)).fetchone()
        if user and check_password_hash(user["password_hash"], request.form["password"]):
            if user["role"] == "staff":
                return _begin_staff_login(conn, user)
            conn.close()
            _fresh_session(user_id=user["id"])
            return redirect(url_for("patient_dashboard"))
        conn.close()
        flash(_t("auth.login_failed", lang=current_lang()), "error")
    return render_template("auth/login.html")


def _otp_pending():
    pending = session.get("otp_pending")
    if not pending or time.time() - pending.get("at", 0) > OTP_SESSION_SECONDS:
        session.pop("otp_pending", None)
        return None
    return pending


@app.route("/otp", methods=["GET", "POST"])
def otp_page():
    pending = _otp_pending()
    if not pending:
        flash(_t("otp.session_expired", lang=current_lang()), "error")
        return redirect(url_for("login"))
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (pending["user_id"],)).fetchone()
    if user is None or user["role"] != "staff":
        conn.close()
        session.pop("otp_pending", None)
        return redirect(url_for("login"))
    lang = current_lang()

    if request.method == "POST":
        result, left = otp.verify(conn, user["id"], pending["purpose"], request.form.get("code", ""))
        if result == "ok":
            if pending["purpose"] == "signup":
                conn.execute("UPDATE users SET status='pending' WHERE id=? AND status='unverified'", (user["id"],))
                conn.commit()
                conn.close()
                session.pop("otp_pending", None)
                flash(_t("auth.staff_pending", lang=lang), "info")
                return redirect(url_for("login"))
            if user["status"] != "active":           # defensive: never log in an unapproved account
                conn.close()
                _fresh_session()
                return redirect(url_for("login"))
            conn.close()
            _fresh_session(user_id=user["id"])
            return redirect(url_for("staff_dashboard"))
        key = {"wrong": "otp.wrong", "expired": "otp.expired", "locked": "otp.locked", "none": "otp.expired"}[result]
        flash(_t(key, lang=lang, n=left), "error")

    wait = otp.seconds_until_resend(conn, user["id"])
    phone = otp.mask_phone(user["contact_number"])
    conn.close()
    return render_template("auth/otp.html", purpose=pending["purpose"], phone=phone, wait=wait)


@app.route("/otp/resend", methods=["POST"])
def otp_resend():
    pending = _otp_pending()
    if not pending:
        return redirect(url_for("login"))
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (pending["user_id"],)).fetchone()
    if user is None or user["role"] != "staff":
        conn.close()
        return redirect(url_for("login"))
    lang = current_lang()
    status, wait = otp.issue(conn, user["id"], pending["purpose"], user["contact_number"])
    conn.close()
    if status == "sent":
        flash(_t("otp.sent_again", lang=lang), "success")
    elif status == "cooldown":
        flash(_t("otp.resend_in", lang=lang, s=wait), "info")
    elif status == "too_many":
        flash(_t("otp.too_many", lang=lang), "error")
    else:
        flash(_t("otp.send_failed", lang=lang), "error")
    return redirect(url_for("otp_page"))


@app.route("/otp/cancel")
def otp_cancel():
    session.pop("otp_pending", None)
    return redirect(url_for("login"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- Patient routes ----------

@app.route("/dashboard")
@login_required(role="patient")
def patient_dashboard(user):
    conn = get_db()
    upcoming = conn.execute(
        "SELECT a.*, c.name AS clinic_name FROM appointments a "
        "JOIN clinics c ON c.id = a.clinic_id "
        "WHERE a.patient_id = ? AND a.status != 'Reschedule' "
        "AND (a.appt_date > ? OR (a.appt_date = ? )) "
        "ORDER BY a.appt_date ASC, a.appt_time ASC LIMIT 1",
        (user["id"], date.today().isoformat(), date.today().isoformat()),
    ).fetchone()
    conn.close()
    return render_template("patient/dashboard.html", upcoming=upcoming)


@app.route("/health-assistant", methods=["GET", "POST"])
@login_required(role="patient")
def health_assistant(user):
    history = session.get("ai_chat", [])

    if request.method == "POST":
        message = request.form.get("message", "").strip()
        if message:
            conn = get_db()
            context = patient_health_context(conn, user["id"])
            conn.close()

            answer = get_health_response(message, patient_context=context, lang=current_lang())
            history.append({"q": message, "a": answer})
            history = history[-10:]  # keep the session light
            session["ai_chat"] = history

        return redirect(url_for("health_assistant"))

    return render_template("patient/health_assistant.html", history=history)


@app.route("/health-assistant/clear")
@login_required(role="patient")
def health_assistant_clear(user):
    session.pop("ai_chat", None)
    return redirect(url_for("health_assistant"))


@app.route("/profile")
@login_required(role="patient")
def patient_profile(user):
    conn = get_db()
    profile = conn.execute(
        "SELECT * FROM patient_profiles WHERE user_id = ?", (user["id"],)
    ).fetchone()
    conn.close()
    return render_template("patient/profile.html", profile=profile)


@app.route("/profile/edit", methods=["GET", "POST"])
@login_required(role="patient")
def patient_profile_edit(user):
    conn = get_db()
    if request.method == "POST":
        f = request.form
        conn.execute(
            "UPDATE patient_profiles SET existing_conditions=?, allergies=?, medications=?, "
            "medical_history=?, address=?, emergency_contact_name=?, emergency_contact_relation=?, "
            "emergency_contact_number=? WHERE user_id=?",
            (f["existing_conditions"], f["allergies"], f["medications"], f["medical_history"],
             f["address"], f["emergency_contact_name"], f["emergency_contact_relation"],
             f["emergency_contact_number"], user["id"]),
        )
        conn.commit()
        conn.close()
        flash(_t("profile.updated", lang=current_lang()), "success")
        return redirect(url_for("patient_profile"))
    profile = conn.execute("SELECT * FROM patient_profiles WHERE user_id = ?", (user["id"],)).fetchone()
    conn.close()
    return render_template("patient/profile_edit.html", profile=profile)


@app.route("/appointments")
@login_required(role="patient")
def patient_appointments(user):
    conn = get_db()
    rows = conn.execute(
        "SELECT a.*, c.name AS clinic_name, u.full_name AS doctor_name FROM appointments a "
        "JOIN clinics c ON c.id = a.clinic_id "
        "JOIN users u ON u.id = a.staff_id "
        "WHERE a.patient_id = ? ORDER BY a.appt_date DESC, a.appt_time DESC",
        (user["id"],),
    ).fetchall()

    context = patient_health_context(conn, user["id"])

    appts = []
    for row in rows:
        appt = dict(row)
        messages = conn.execute(
            "SELECT role, content FROM appointment_messages WHERE appointment_id=? ORDER BY id ASC",
            (appt["id"],),
        ).fetchall()

        has_patient_msg = any(m["role"] == "patient" for m in messages)
        has_ai_msg = any(m["role"] == "ai" for m in messages)
        changed = False

        if appt["reason"] and not has_patient_msg:
            conn.execute(
                "INSERT INTO appointment_messages (appointment_id, role, content) VALUES (?, 'patient', ?)",
                (appt["id"], appt["reason"]),
            )
            changed = True

        if appt["reason"] and not has_ai_msg and not appt["ai_chat_closed"]:
            # Reuse an older database's ai_note if present; otherwise generate fresh.
            # Retried on every visit until it succeeds — e.g. if Ollama was briefly
            # down on the first visit, a later visit will pick it up instead of
            # leaving the patient with no reply forever.
            note = appt["ai_note"] or get_appointment_note(appt["reason"], patient_context=context, lang=current_lang())
            if note:
                conn.execute(
                    "INSERT INTO appointment_messages (appointment_id, role, content) VALUES (?, 'ai', ?)",
                    (appt["id"], note),
                )
                changed = True

        if changed:
            conn.commit()
            messages = conn.execute(
                "SELECT role, content FROM appointment_messages WHERE appointment_id=? ORDER BY id ASC",
                (appt["id"],),
            ).fetchall()

        appt["messages"] = messages
        appts.append(appt)

    conn.close()
    return render_template("patient/appointments.html", appointments=appts)


@app.route("/appointments/<int:appt_id>/message", methods=["POST"])
@login_required(role="patient")
def appointment_message(user, appt_id):
    message = request.form.get("message", "").strip()
    conn = get_db()
    appt = conn.execute(
        "SELECT id, ai_chat_closed FROM appointments WHERE id=? AND patient_id=?", (appt_id, user["id"])
    ).fetchone()
    if not appt:
        conn.close()
        abort(404)

    if message and not appt["ai_chat_closed"]:      # an ended conversation stays ended until the patient resumes it
        context = patient_health_context(conn, user["id"])
        answer = get_health_response(message, patient_context=context, lang=current_lang())
        conn.execute(
            "INSERT INTO appointment_messages (appointment_id, role, content) VALUES (?, 'patient', ?)",
            (appt_id, message),
        )
        conn.execute(
            "INSERT INTO appointment_messages (appointment_id, role, content) VALUES (?, 'ai', ?)",
            (appt_id, answer),
        )
        conn.commit()
    conn.close()
    return redirect(url_for("patient_appointments", _anchor=f"appt-{appt_id}"))


@app.route("/appointments/<int:appt_id>/chat/<action>", methods=["POST"])
@login_required(role="patient")
def appointment_chat_toggle(user, appt_id, action):
    """End or resume the AI conversation on one appointment. The appointment itself is never touched."""
    if action not in ("end", "resume"):
        abort(404)
    conn = get_db()
    cur = conn.execute(
        "UPDATE appointments SET ai_chat_closed=? WHERE id=? AND patient_id=?",
        (1 if action == "end" else 0, appt_id, user["id"]),
    )
    conn.commit()
    conn.close()
    if cur.rowcount == 0:
        abort(404)
    return redirect(url_for("patient_appointments", _anchor=f"appt-{appt_id}"))


@app.route("/appointment/new", methods=["GET", "POST"])
@login_required(role="patient")
def new_appointment_clinic(user):
    conn = get_db()
    if request.method == "POST":
        session["new_appt"] = {"clinic_id": request.form["clinic_id"]}
        conn.close()
        return redirect(url_for("new_appointment_datetime"))
    clinics = conn.execute("SELECT * FROM clinics ORDER BY name").fetchall()
    conn.close()
    return render_template("patient/new_appointment_clinic.html", clinics=clinics)


@app.route("/appointment/new/datetime", methods=["GET", "POST"])
@login_required(role="patient")
def new_appointment_datetime(user):
    draft = session.get("new_appt")
    if not draft:
        return redirect(url_for("new_appointment_clinic"))

    conn = get_db()
    clinic_id = draft["clinic_id"]
    # pick the first staff member assigned to this clinic (kept simple / single-doctor-per-clinic demo)
    staff = conn.execute(
        "SELECT u.* FROM users u JOIN staff_clinics sc ON sc.staff_id = u.id "
        "WHERE sc.clinic_id = ? AND u.role = 'staff' AND u.status = 'active' ORDER BY u.id LIMIT 1", (clinic_id,)
    ).fetchone()

    if request.method == "POST":
        draft["staff_id"] = staff["id"] if staff else None
        draft["appt_date"] = request.form["appt_date"]
        draft["appt_time"] = request.form["appt_time"]
        session["new_appt"] = draft
        conn.close()
        return redirect(url_for("new_appointment_reason"))

    upcoming_dates = daterange_next_days(14)
    date_options = []
    if staff:
        for d in upcoming_dates:
            if any_availability(conn, staff["id"], d):
                date_options.append(d)

    selected_date_str = request.args.get("date")
    selected_date = date.fromisoformat(selected_date_str) if selected_date_str else (date_options[0] if date_options else None)
    day_slots = generate_slots_for_day(conn, staff["id"], selected_date) if staff and selected_date else []
    available_slots = [s for s in day_slots if s["available"]]
    morning_slots = [s for s in available_slots if int(s["time"][:2]) < 12]
    afternoon_slots = [s for s in available_slots if int(s["time"][:2]) >= 12]
    calendar_months = build_calendar_months(date_options) if date_options else []
    conn.close()

    return render_template(
        "patient/new_appointment_datetime.html",
        staff=staff, calendar_months=calendar_months, selected_date=selected_date,
        morning_slots=morning_slots, afternoon_slots=afternoon_slots,
    )


@app.route("/appointment/new/reason", methods=["GET", "POST"])
@login_required(role="patient")
def new_appointment_reason(user):
    draft = session.get("new_appt")
    if not draft or "appt_date" not in draft:
        return redirect(url_for("new_appointment_clinic"))

    if request.method == "POST":
        draft["reason"] = request.form["reason"]
        session["new_appt"] = draft
        return redirect(url_for("new_appointment_confirm"))

    return render_template("patient/new_appointment_reason.html")


@app.route("/appointment/new/confirm", methods=["GET", "POST"])
@login_required(role="patient")
def new_appointment_confirm(user):
    draft = session.get("new_appt")
    if not draft or "reason" not in draft:
        return redirect(url_for("new_appointment_clinic"))

    conn = get_db()
    clinic = conn.execute("SELECT * FROM clinics WHERE id=?", (draft["clinic_id"],)).fetchone()
    staff = conn.execute("SELECT * FROM users WHERE id=?", (draft["staff_id"],)).fetchone()
    profile = conn.execute("SELECT * FROM patient_profiles WHERE user_id=?", (user["id"],)).fetchone()

    if request.method == "POST":
        cur = conn.execute(
            "INSERT INTO appointments (patient_id, staff_id, clinic_id, appt_date, appt_time, reason, status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'Pending')",
            (user["id"], draft["staff_id"], draft["clinic_id"], draft["appt_date"], draft["appt_time"], draft["reason"]),
        )
        appt_id = cur.lastrowid
        conn.commit()
        conn.close()
        # The AI note is generated lazily on the My Appointments page instead of here,
        # so submitting an appointment stays instant — see patient_appointments().
        session.pop("new_appt", None)
        flash(_t("wizard.submitted", lang=current_lang()), "success")
        return redirect(url_for("patient_appointments"))

    conn.close()
    return render_template(
        "patient/new_appointment_confirm.html",
        draft=draft, clinic=clinic, staff=staff, profile=profile,
    )


@app.route("/appointment/new/cancel")
@login_required(role="patient")
def new_appointment_cancel(user):
    session.pop("new_appt", None)
    return redirect(url_for("patient_dashboard"))


# ---------- Staff routes ----------

@app.route("/staff/dashboard")
@login_required(role="staff")
def staff_dashboard(user):
    conn = get_db()
    pending_count = conn.execute(
        "SELECT COUNT(*) c FROM appointments WHERE staff_id=? AND status='Pending'", (user["id"],)
    ).fetchone()["c"]
    today_appts = conn.execute(
        "SELECT a.*, u.full_name AS patient_name FROM appointments a "
        "JOIN users u ON u.id = a.patient_id "
        "WHERE a.staff_id=? AND a.appt_date=? AND a.status != 'Reschedule' "
        "ORDER BY a.appt_time ASC",
        (user["id"], date.today().isoformat()),
    ).fetchall()
    gateway = None
    if sms_mode() == "esp32":
        gateway = {**sms_gateway.gateway_status(conn), **sms_gateway.counts(conn)}
    pending_accounts = conn.execute(
        "SELECT COUNT(*) AS n FROM users WHERE role='staff' AND status='pending'"
    ).fetchone()["n"]
    conn.close()
    return render_template("staff/dashboard.html", pending_count=pending_count, today_appts=today_appts,
                           gateway=gateway, pending_accounts=pending_accounts)


@app.route("/staff/requests")
@login_required(role="staff")
def staff_requests(user):
    conn = get_db()
    requests_ = conn.execute(
        "SELECT a.*, u.full_name AS patient_name, c.name AS clinic_name FROM appointments a "
        "JOIN users u ON u.id = a.patient_id "
        "JOIN clinics c ON c.id = a.clinic_id "
        "WHERE a.staff_id=? AND a.status='Pending' "
        "ORDER BY a.appt_date ASC, a.appt_time ASC",
        (user["id"],),
    ).fetchall()
    conn.close()
    return render_template("staff/requests.html", requests=requests_)


@app.route("/staff/requests/<int:appt_id>", methods=["GET", "POST"])
@login_required(role="staff")
def staff_request_detail(user, appt_id):
    conn = get_db()
    appt = conn.execute(
        "SELECT a.*, c.name AS clinic_name FROM appointments a "
        "JOIN clinics c ON c.id = a.clinic_id WHERE a.id=? AND a.staff_id=?",
        (appt_id, user["id"]),
    ).fetchone()
    if not appt:
        conn.close()
        abort(404)

    patient = conn.execute("SELECT * FROM users WHERE id=?", (appt["patient_id"],)).fetchone()
    profile = conn.execute("SELECT * FROM patient_profiles WHERE user_id=?", (appt["patient_id"],)).fetchone()

    if request.method == "POST":
        if appt["status"] != "Pending":
            # Already decided (e.g. a double-click or replayed form): keep the original decision and timestamp.
            conn.close()
            return redirect(url_for("staff_requests"))
        new_status = "Approved" if request.form["decision"] == "approve" else "Reschedule"
        conn.execute(
            "UPDATE appointments SET status=?, decided_at=? WHERE id=?",
            (new_status, datetime.now().strftime("%Y-%m-%d %H:%M"), appt_id),
        )
        conn.commit()
        notify.create(conn, appt["patient_id"], "approved" if new_status == "Approved" else "reschedule", appt_id)
        conn.close()

        if patient["contact_number"]:
            message = appointment_status_message(new_status, appt["clinic_name"], appt["appt_date"], appt["appt_time"])
            send_sms(patient["contact_number"], message)

        status_label = _t(f"status.{new_status.lower()}", lang=current_lang())
        flash_key = "appts.sms_queued" if sms_mode() == "esp32" else "appts.notified_sms"
        flash(_t(flash_key, lang=current_lang(), status=status_label), "toast-success" if new_status == "Approved" else "toast-warn")
        return redirect(url_for("staff_requests"))

    conn.close()
    return render_template("staff/request_detail.html", appt=appt, patient=patient, profile=profile)


@app.route("/staff/history")
@login_required(role="staff")
def staff_history(user):
    """Everything this staff member has already decided: approved, or sent back to reschedule."""
    status_filter = request.args.get("status", "all")
    conn = get_db()

    counts = {"Approved": 0, "Reschedule": 0}
    for row in conn.execute(
        "SELECT status, COUNT(*) AS n FROM appointments WHERE staff_id=? AND status IN ('Approved','Reschedule') GROUP BY status",
        (user["id"],),
    ).fetchall():
        counts[row["status"]] = row["n"]

    sql = (
        "SELECT a.*, u.full_name AS patient_name, c.name AS clinic_name FROM appointments a "
        "JOIN users u ON u.id = a.patient_id JOIN clinics c ON c.id = a.clinic_id "
        "WHERE a.staff_id=? AND a.status IN ('Approved','Reschedule') "
    )
    params = [user["id"]]
    if status_filter == "approved":
        sql += "AND a.status='Approved' "
    elif status_filter == "reschedule":
        sql += "AND a.status='Reschedule' "
    else:
        status_filter = "all"
    # Newest decision first; rows decided before decided_at existed fall back to when they were booked.
    sql += "ORDER BY COALESCE(a.decided_at, a.created_at) DESC, a.id DESC"
    items = conn.execute(sql, params).fetchall()
    conn.close()

    return render_template(
        "staff/history.html", items=items, status_filter=status_filter,
        count_all=counts["Approved"] + counts["Reschedule"],
        count_approved=counts["Approved"], count_reschedule=counts["Reschedule"],
    )


@app.route("/staff/schedule", methods=["GET", "POST"])
@login_required(role="staff")
def staff_schedule(user):
    conn = get_db()
    if request.method == "POST":
        conn.execute(
            "INSERT INTO schedules (staff_id, weekday, start_time, end_time) VALUES (?,?,?,?)",
            (user["id"], int(request.form["weekday"]), request.form["start_time"], request.form["end_time"]),
        )
        conn.commit()
        conn.close()
        flash(_t("staff.schedule_added", lang=current_lang()), "success")
        return redirect(url_for("staff_schedule"))

    rows = conn.execute(
        "SELECT * FROM schedules WHERE staff_id=? ORDER BY weekday, start_time", (user["id"],)
    ).fetchall()
    # The database stores weekday 0 = Monday .. 6 = Sunday; the page lists Sunday first.
    weekday_keys = {0: "day.monday", 1: "day.tuesday", 2: "day.wednesday", 3: "day.thursday", 4: "day.friday", 5: "day.saturday", 6: "day.sunday"}
    weekday_order = [6, 0, 1, 2, 3, 4, 5]
    by_day = {i: [] for i in range(7)}
    for r in rows:
        by_day[r["weekday"]].append(r)

    # "Manage Specific Dates" calendar: any upcoming date whose weekday has a
    # recurring schedule block is clickable, so staff can mark individual
    # time slots fully booked for that exact date. Shown one month at a time,
    # with arrows to page between months (no going back before the current month).
    schedulable_weekdays = weekdays_with_schedule(conn, user["id"])
    horizon_days = 365
    manageable_dates = [d for d in daterange_next_days(horizon_days) if d.weekday() in schedulable_weekdays]
    manageable_set = set(manageable_dates)

    today = date.today()
    last_day = today + timedelta(days=horizon_days - 1)
    min_ym, max_ym = (today.year, today.month), (last_day.year, last_day.month)

    selected_date = None
    date_param = request.args.get("date")
    if date_param:
        try:
            candidate = date.fromisoformat(date_param)
            if candidate in manageable_set:
                selected_date = candidate
        except ValueError:
            pass

    view_ym = None
    month_param = request.args.get("month")
    if month_param:
        try:
            y_str, m_str = month_param.split("-")
            view_ym = (int(y_str), int(m_str))
            date(view_ym[0], view_ym[1], 1)  # validates the month number
        except (ValueError, TypeError):
            view_ym = None
    if view_ym is None:
        anchor = selected_date or (manageable_dates[0] if manageable_dates else today)
        view_ym = (anchor.year, anchor.month)
    view_ym = max(min_ym, min(max_ym, view_ym))  # clamp into the allowed range

    # Paging to another month: pick that month's first manageable date so the slot list is never stale.
    if selected_date is None or (selected_date.year, selected_date.month) != view_ym:
        in_month = [d for d in manageable_dates if (d.year, d.month) == view_ym]
        selected_date = in_month[0] if in_month else None

    calendar_month = build_month(view_ym[0], view_ym[1], manageable_dates) if manageable_dates else None
    prev_ym = shift_month(*view_ym, -1) if view_ym > min_ym else None
    next_ym = shift_month(*view_ym, 1) if view_ym < max_ym else None
    fmt = lambda ym: f"{ym[0]:04d}-{ym[1]:02d}" if ym else None

    day_slot_statuses = slot_statuses_for_day(conn, user["id"], selected_date) if selected_date else []

    conn.close()
    return render_template(
        "staff/schedule.html", weekday_keys=weekday_keys, weekday_order=weekday_order, by_day=by_day,
        morning_statuses=[x for x in day_slot_statuses if int(x["time"][:2]) < 12],
        afternoon_statuses=[x for x in day_slot_statuses if int(x["time"][:2]) >= 12],
        calendar_month=calendar_month, prev_month=fmt(prev_ym), next_month=fmt(next_ym),
        selected_date=selected_date, day_slot_statuses=day_slot_statuses,
    )


@app.route("/staff/schedule/toggle-block", methods=["POST"])
@login_required(role="staff")
def staff_schedule_toggle_block(user):
    slot_date = request.form["slot_date"]
    slot_time = request.form["slot_time"]
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM blocked_slots WHERE staff_id=? AND slot_date=? AND slot_time=?",
        (user["id"], slot_date, slot_time),
    ).fetchone()
    if existing:
        conn.execute("DELETE FROM blocked_slots WHERE id=?", (existing["id"],))
    else:
        conn.execute(
            "INSERT OR IGNORE INTO blocked_slots (staff_id, slot_date, slot_time) VALUES (?,?,?)",
            (user["id"], slot_date, slot_time),
        )
    conn.commit()
    conn.close()
    return redirect(url_for("staff_schedule", date=slot_date))


@app.route("/staff/schedule/<int:sched_id>/delete", methods=["POST"])
@login_required(role="staff")
def staff_schedule_delete(user, sched_id):
    conn = get_db()
    conn.execute("DELETE FROM schedules WHERE id=? AND staff_id=?", (sched_id, user["id"]))
    conn.commit()
    conn.close()
    return redirect(url_for("staff_schedule"))


# ---------- SMS gateway (ESP32 + SIM800L) ----------

def _device_authorized():
    """The gateway authenticates with a shared secret in the X-Device-Key header (env SMS_DEVICE_KEY)."""
    expected = os.environ.get("SMS_DEVICE_KEY", "")
    given = request.headers.get("X-Device-Key", "")
    return bool(expected) and hmac.compare_digest(expected.encode(), given.encode())


@app.route("/api/sms/next")
def api_sms_next():
    """Gateway polls here. 204 = nothing to send. 200 = one line:  <id> TAB <+63 number> TAB <text>"""
    if not _device_authorized():
        return Response("forbidden", status=403, mimetype="text/plain")
    conn = get_db()
    try:
        rssi = request.args.get("rssi", type=int)
        sms_gateway.touch_gateway(conn, rssi)
        row = sms_gateway.claim_next(conn)
    finally:
        conn.close()
    if row is None:
        return Response(status=204)
    return Response(f"{row['id']}\t{row['to_number']}\t{row['message']}", mimetype="text/plain")


@app.route("/api/sms/<int:sms_id>/result", methods=["POST"])
def api_sms_result(sms_id):
    """Gateway reports the outcome of the message it claimed: form fields ok=1|0 and (optional) error=<text>."""
    if not _device_authorized():
        return Response("forbidden", status=403, mimetype="text/plain")
    conn = get_db()
    try:
        sms_gateway.touch_gateway(conn)
        accepted = sms_gateway.report_result(
            conn, sms_id, request.form.get("ok") == "1", request.form.get("error", "")
        )
    finally:
        conn.close()
    return Response("ok" if accepted else "unknown or not pending", status=200 if accepted else 404, mimetype="text/plain")


@app.route("/staff/sms")
@login_required(role="staff")
def staff_sms(user):
    conn = get_db()
    data = dict(
        enabled=(sms_mode() == "esp32"), mode=sms_mode(),
        gateway=sms_gateway.gateway_status(conn), counts=sms_gateway.counts(conn),
        messages=sms_gateway.recent(conn, 25),
    )
    conn.close()
    return render_template("staff/sms.html", **data)


@app.route("/staff/clinics", methods=["GET", "POST"])
@login_required(role="staff")
def staff_clinics(user):
    conn = get_db()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        address = request.form.get("address", "").strip()
        if name:
            conn.execute("INSERT INTO clinics (name, address) VALUES (?, ?)", (name, address))
            conn.commit()
            flash(_t("clinics.added", lang=current_lang()), "success")
        conn.close()
        return redirect(url_for("staff_clinics"))

    clinics = conn.execute(
        "SELECT c.*, "
        "(SELECT COUNT(*) FROM staff_clinics sc JOIN users u ON u.id = sc.staff_id "
        "  WHERE sc.clinic_id = c.id AND u.status = 'active') AS staff_count, "
        "(SELECT COUNT(*) FROM appointments a WHERE a.clinic_id = c.id) AS appt_count "
        "FROM clinics c ORDER BY c.name"
    ).fetchall()
    conn.close()
    return render_template("staff/clinics.html", clinics=clinics)


@app.route("/staff/clinics/<int:clinic_id>/edit", methods=["GET", "POST"])
@login_required(role="staff")
def staff_clinic_edit(user, clinic_id):
    conn = get_db()
    clinic = conn.execute("SELECT * FROM clinics WHERE id=?", (clinic_id,)).fetchone()
    if not clinic:
        conn.close()
        abort(404)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        address = request.form.get("address", "").strip()
        if name:
            conn.execute("UPDATE clinics SET name=?, address=? WHERE id=?", (name, address, clinic_id))
            conn.commit()
            flash(_t("clinics.updated", lang=current_lang()), "success")
        conn.close()
        return redirect(url_for("staff_clinics"))

    conn.close()
    return render_template("staff/clinic_edit.html", clinic=clinic)


@app.route("/staff/clinics/<int:clinic_id>/delete", methods=["POST"])
@login_required(role="staff")
def staff_clinic_delete(user, clinic_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM clinics WHERE id=?", (clinic_id,))
        conn.commit()
        flash(_t("clinics.deleted", lang=current_lang()), "success")
    except sqlite3.IntegrityError:
        # Clinic is still referenced by a staff profile or past/pending appointments —
        # refuse rather than orphan that data.
        conn.rollback()
        flash(_t("clinics.in_use", lang=current_lang()), "error")
    conn.close()
    return redirect(url_for("staff_clinics"))


@app.route("/staff/profile", methods=["GET", "POST"])
@login_required(role="staff")
def staff_profile(user):
    conn = get_db()
    clinics = conn.execute("SELECT * FROM clinics ORDER BY name").fetchall()
    if request.method == "POST":
        f = request.form
        lang = current_lang()
        if not to_e164_ph(f.get("contact_number")):
            conn.close()                            # verification codes are texted to this number — it must be reachable
            flash(_t("auth.staff_phone_required", lang=lang), "error")
            return redirect(url_for("staff_profile"))
        conn.execute("UPDATE users SET full_name=?, contact_number=? WHERE id=?",
                     (f["full_name"], f["contact_number"], user["id"]))
        conn.execute("UPDATE staff_profiles SET specialization=? WHERE user_id=?", (f["specialization"], user["id"]))
        valid_ids = {c["id"] for c in clinics}
        chosen = {int(x) for x in f.getlist("clinic_ids") if x.isdigit()} & valid_ids
        current = {r["clinic_id"] for r in conn.execute("SELECT clinic_id FROM staff_clinics WHERE staff_id=?", (user["id"],))}
        for cid in current - chosen:
            conn.execute("DELETE FROM staff_clinics WHERE staff_id=? AND clinic_id=?", (user["id"], cid))
        for cid in chosen - current:
            conn.execute("INSERT INTO staff_clinics (staff_id, clinic_id) VALUES (?, ?)", (user["id"], cid))
        conn.commit()
        conn.close()
        flash(_t("profile.updated", lang=lang), "success")
        return redirect(url_for("staff_profile"))

    profile = conn.execute("SELECT * FROM staff_profiles WHERE user_id=?", (user["id"],)).fetchone()
    my_clinic_ids = {r["clinic_id"] for r in conn.execute("SELECT clinic_id FROM staff_clinics WHERE staff_id=?", (user["id"],))}
    conn.close()
    return render_template("staff/profile.html", profile=profile, clinics=clinics, my_clinic_ids=my_clinic_ids)


# ---------- Reviewing new doctor/staff accounts ----------

@app.route("/staff/accounts")
@login_required(role="staff")
def staff_accounts(user):
    conn = get_db()
    pending = conn.execute(
        "SELECT u.*, sp.specialization, "
        "(SELECT group_concat(c.name, ', ') FROM staff_clinics sc JOIN clinics c ON c.id = sc.clinic_id "
        "  WHERE sc.staff_id = u.id) AS clinic_names "
        "FROM users u LEFT JOIN staff_profiles sp ON sp.user_id = u.id "
        "WHERE u.role='staff' AND u.status='pending' ORDER BY u.id"
    ).fetchall()
    decided = conn.execute(
        "SELECT u.*, r.full_name AS reviewer_name FROM users u LEFT JOIN users r ON r.id = u.reviewed_by "
        "WHERE u.role='staff' AND u.status IN ('active','rejected') AND u.reviewed_at IS NOT NULL "
        "ORDER BY u.reviewed_at DESC, u.id DESC LIMIT 20"
    ).fetchall()
    conn.close()
    return render_template("staff/accounts.html", pending=pending, decided=decided)


@app.route("/staff/accounts/<int:account_id>/decision", methods=["POST"])
@login_required(role="staff")
def staff_account_decision(user, account_id):
    if account_id == user["id"]:
        abort(403)                                   # nobody approves their own account
    conn = get_db()
    target = conn.execute("SELECT * FROM users WHERE id=? AND role='staff'", (account_id,)).fetchone()
    if target is None or target["status"] != "pending":
        conn.close()
        return redirect(url_for("staff_accounts"))   # already decided (double click) or unknown: leave it alone
    approve = request.form.get("decision") == "approve"
    conn.execute(
        "UPDATE users SET status=?, reviewed_by=?, reviewed_at=? WHERE id=?",
        ("active" if approve else "rejected", user["id"], datetime.now().strftime("%Y-%m-%d %H:%M"), account_id),
    )
    conn.commit()
    conn.close()
    send_sms(target["contact_number"], (
        "MediAlabat: Your doctor/staff account was approved. You can now log in."
        if approve else
        "MediAlabat: Your doctor/staff account request was not approved. Please contact the clinic."
    ))
    flash(_t("accounts.approved_flash" if approve else "accounts.rejected_flash", lang=current_lang(), name=target["full_name"]), "toast-success" if approve else "toast-warn")
    return redirect(url_for("staff_accounts"))


# ---------- Patient notifications (toasts) ----------

@app.route("/api/notifications")
@login_required(role="patient")
def api_notifications(user):
    """Polled by the page every few seconds; returns (and marks as seen) any new 'approved / reschedule' notices."""
    conn = get_db()
    rows = notify.take_unseen(conn, user["id"])
    conn.close()
    lang = current_lang()
    return jsonify(items=[
        {
            "id": r["id"],
            "kind": r["kind"],
            "text": _t("notify." + r["kind"], lang=lang, clinic=r["clinic_name"] or "", date=r["appt_date"] or "", time=r["appt_time"] or ""),
        }
        for r in rows
    ])


if __name__ == "__main__":
    init_db()
    # app.run(debug=True, host="192.168.80.159", port=5000)
    # Defaults are unchanged (127.0.0.1:5000, debug on). To let the ESP32 reach this PC over Wi-Fi run e.g.
    #   HOST=0.0.0.0 FLASK_DEBUG=0 python app.py        (Windows PowerShell:  $env:HOST="0.0.0.0"; $env:FLASK_DEBUG="0"; python app.py)
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    if debug and host not in ("127.0.0.1", "localhost"):
        print("WARNING: debug mode is on while listening on the network. Set FLASK_DEBUG=0 — the debugger can run code.")
    app.run(host=host, port=port, debug=debug)
