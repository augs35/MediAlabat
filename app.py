import functools
import sqlite3
from datetime import datetime, date, timedelta

from flask import (
    Flask, render_template, request, redirect, url_for, session, flash, abort
)
from werkzeug.security import generate_password_hash, check_password_hash

from db import get_db, init_db
from slots import (
    daterange_next_days, generate_slots_for_day, any_availability,
    time_options, build_calendar_months, slot_statuses_for_day, weekdays_with_schedule,
    build_month, shift_month,
)
from sms import send_sms, appointment_status_message
from health_ai import get_health_response, get_appointment_note
from translations import t as _t, SUPPORTED_LANGS, DEFAULT_LANG

app = Flask(__name__)
app.secret_key = "dev-secret-change-me"  # replace with a real secret in production


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
    if request.method == "POST":
        f = request.form
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (f["email"],)).fetchone()
        if existing:
            flash(_t("auth.email_exists", lang=current_lang()), "error")
            clinics = conn.execute("SELECT * FROM clinics ORDER BY name").fetchall()
            conn.close()
            return render_template("auth/staff_signup.html", form=f, clinics=clinics)

        full_name = join_name(f["first_name"], f.get("middle_name", ""), f["last_name"])
        cur = conn.execute(
            "INSERT INTO users (role, full_name, first_name, middle_name, last_name, email, password_hash, contact_number) "
            "VALUES ('staff', ?, ?, ?, ?, ?, ?, ?)",
            (full_name, f["first_name"], f.get("middle_name", ""), f["last_name"],
             f["email"], generate_password_hash(f["password"]), f["contact_number"]),
        )
        user_id = cur.lastrowid
        clinic_id = f.get("clinic_id") or None
        conn.execute(
            "INSERT INTO staff_profiles (user_id, specialization, clinic_id) VALUES (?, ?, ?)",
            (user_id, f.get("specialization", ""), clinic_id),
        )
        conn.commit()
        conn.close()
        flash(_t("auth.signup_success", lang=current_lang()), "success")
        return redirect(url_for("login"))

    clinics = conn.execute("SELECT * FROM clinics ORDER BY name").fetchall()
    conn.close()
    return render_template("auth/staff_signup.html", form={}, clinics=clinics)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email = ?", (request.form["email"],)).fetchone()
        conn.close()
        if user and check_password_hash(user["password_hash"], request.form["password"]):
            session["user_id"] = user["id"]
            return redirect(url_for("staff_dashboard") if user["role"] == "staff" else url_for("patient_dashboard"))
        flash(_t("auth.login_failed", lang=current_lang()), "error")
    return render_template("auth/login.html")


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

        if appt["reason"] and not has_ai_msg:
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
        "SELECT id FROM appointments WHERE id=? AND patient_id=?", (appt_id, user["id"])
    ).fetchone()
    if not appt:
        conn.close()
        abort(404)

    if message:
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
        "SELECT u.* FROM users u JOIN staff_profiles sp ON sp.user_id = u.id "
        "WHERE sp.clinic_id = ? LIMIT 1", (clinic_id,)
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
    conn.close()
    return render_template("staff/dashboard.html", pending_count=pending_count, today_appts=today_appts)


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
        conn.close()

        if patient["contact_number"]:
            message = appointment_status_message(new_status, appt["clinic_name"], appt["appt_date"], appt["appt_time"])
            send_sms(patient["contact_number"], message)

        status_label = _t(f"status.{new_status.lower()}", lang=current_lang())
        flash(_t("appts.notified_sms", lang=current_lang(), status=status_label), "success")
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
    weekday_keys = ["day.monday", "day.tuesday", "day.wednesday", "day.thursday", "day.friday", "day.saturday", "day.sunday"]
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
        "staff/schedule.html", weekday_keys=weekday_keys, by_day=by_day,
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
        "(SELECT COUNT(*) FROM staff_profiles sp WHERE sp.clinic_id = c.id) AS staff_count, "
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
    if request.method == "POST":
        f = request.form
        conn.execute("UPDATE users SET full_name=?, contact_number=? WHERE id=?",
                     (f["full_name"], f["contact_number"], user["id"]))
        conn.execute("UPDATE staff_profiles SET specialization=?, clinic_id=? WHERE user_id=?",
                     (f["specialization"], f["clinic_id"], user["id"]))
        conn.commit()
        conn.close()
        flash(_t("profile.updated", lang=current_lang()), "success")
        return redirect(url_for("staff_profile"))

    profile = conn.execute("SELECT * FROM staff_profiles WHERE user_id=?", (user["id"],)).fetchone()
    clinics = conn.execute("SELECT * FROM clinics ORDER BY name").fetchall()
    conn.close()
    return render_template("staff/profile.html", profile=profile, clinics=clinics)


if __name__ == "__main__":
    init_db()
    # app.run(debug=True, host="192.168.80.159", port=5000)
    app.run(host="0.0.0.0", port=5000, debug=False)
