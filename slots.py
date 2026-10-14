import calendar as _calendar
from datetime import datetime, timedelta, date, time


def daterange_next_days(n=14):
    """Return the next n calendar dates starting today, as date objects."""
    today = date.today()
    return [today + timedelta(days=i) for i in range(n)]


def _time_to_minutes(t):
    h, m = map(int, t.split(":"))
    return h * 60 + m


def _minutes_to_time(mins):
    h, m = divmod(mins, 60)
    return f"{h:02d}:{m:02d}"


def generate_slots_for_day(conn, staff_id, the_date):
    """
    Given a staff member and a date, return a list of dicts:
    [{"time": "09:00", "label": "9:00 AM", "available": True/False}, ...]
    based on that staff's recurring weekly schedule minus already-booked appointments.
    """
    weekday = the_date.weekday()  # Monday=0
    rows = conn.execute(
        "SELECT start_time, end_time, slot_minutes FROM schedules "
        "WHERE staff_id = ? AND weekday = ?",
        (staff_id, weekday),
    ).fetchall()

    if not rows:
        return []

    # A slot only closes once staff have actually approved someone for it, or staff
    # have manually marked it as fully booked. A patient simply requesting a time
    # (status='Pending') does NOT lock it out for other patients — staff decide who
    # gets it when they review requests.
    booked = {
        r["appt_time"]
        for r in conn.execute(
            "SELECT appt_time FROM appointments WHERE staff_id = ? AND appt_date = ? "
            "AND status = 'Approved'",
            (staff_id, the_date.isoformat()),
        ).fetchall()
    }
    booked |= {
        r["slot_time"]
        for r in conn.execute(
            "SELECT slot_time FROM blocked_slots WHERE staff_id = ? AND slot_date = ?",
            (staff_id, the_date.isoformat()),
        ).fetchall()
    }

    now = datetime.now()
    is_today = the_date == date.today()

    slots = []
    seen = set()
    for row in rows:
        start = _time_to_minutes(row["start_time"])
        end = _time_to_minutes(row["end_time"])
        step = row["slot_minutes"] or 30
        cur = start
        while cur + step <= end:
            t = _minutes_to_time(cur)
            if t not in seen:
                seen.add(t)
                available = t not in booked
                if is_today:
                    slot_dt = datetime.combine(the_date, time(cur // 60, cur % 60))
                    if slot_dt <= now:
                        available = False
                slots.append({"time": t, "label": _format_label(t), "available": available})
            cur += step

    slots.sort(key=lambda s: s["time"])
    return slots


def _format_label(t):
    h, m = map(int, t.split(":"))
    period = "AM" if h < 12 else "PM"
    h12 = h % 12
    if h12 == 0:
        h12 = 12
    return f"{h12}:{m:02d} {period}"


def any_availability(conn, staff_id, the_date):
    return any(s["available"] for s in generate_slots_for_day(conn, staff_id, the_date))


def slot_statuses_for_day(conn, staff_id, the_date):
    """
    Like generate_slots_for_day, but for the staff "manage this date" view —
    returns each slot's status so the UI knows whether a toggle makes sense:
    {"time", "label", "status"} where status is one of:
      "open"     — free, staff can mark it fully booked
      "blocked"  — staff marked it fully booked, can reopen it
      "approved" — a real approved appointment occupies it, not toggleable
      "past"     — already past (only possible for today), not toggleable
    """
    weekday = the_date.weekday()
    rows = conn.execute(
        "SELECT start_time, end_time, slot_minutes FROM schedules WHERE staff_id = ? AND weekday = ?",
        (staff_id, weekday),
    ).fetchall()
    if not rows:
        return []

    approved = {
        r["appt_time"]
        for r in conn.execute(
            "SELECT appt_time FROM appointments WHERE staff_id = ? AND appt_date = ? AND status = 'Approved'",
            (staff_id, the_date.isoformat()),
        ).fetchall()
    }
    blocked = {
        r["slot_time"]
        for r in conn.execute(
            "SELECT slot_time FROM blocked_slots WHERE staff_id = ? AND slot_date = ?",
            (staff_id, the_date.isoformat()),
        ).fetchall()
    }

    now = datetime.now()
    is_today = the_date == date.today()

    slots = []
    seen = set()
    for row in rows:
        start = _time_to_minutes(row["start_time"])
        end = _time_to_minutes(row["end_time"])
        step = row["slot_minutes"] or 30
        cur = start
        while cur + step <= end:
            t = _minutes_to_time(cur)
            if t not in seen:
                seen.add(t)
                if is_today:
                    slot_dt = datetime.combine(the_date, time(cur // 60, cur % 60))
                    if slot_dt <= now:
                        slots.append({"time": t, "label": _format_label(t), "status": "past"})
                        cur += step
                        continue
                if t in approved:
                    status = "approved"
                elif t in blocked:
                    status = "blocked"
                else:
                    status = "open"
                slots.append({"time": t, "label": _format_label(t), "status": status})
            cur += step

    slots.sort(key=lambda s: s["time"])
    return slots


def weekdays_with_schedule(conn, staff_id):
    """Set of weekday ints (0=Monday) that have at least one recurring schedule block."""
    return {
        r["weekday"]
        for r in conn.execute("SELECT DISTINCT weekday FROM schedules WHERE staff_id = ?", (staff_id,)).fetchall()
    }


def time_options(step_minutes=30):
    """All times in a day as {"value": "HH:MM", "label": "9:00 AM"} for <select> dropdowns."""
    options = []
    cur = 0
    while cur < 24 * 60:
        t = _minutes_to_time(cur)
        options.append({"value": t, "label": _format_label(t)})
        cur += step_minutes
    return options


def build_calendar_months(available_dates):
    """
    Build a month-by-month calendar (Monday-first) covering the span of
    `available_dates` (a list of date objects), for rendering an actual
    calendar grid in the template instead of a flat date strip.

    Returns a list of {"label": "October 2026", "weeks": [[{date, in_range,
    available}, ...7 per week], ...]}. Days outside the booking window still
    render (grayed out) so the grid looks like a real calendar, but only
    `available` days are meant to be clickable.
    """
    if not available_dates:
        return []

    available_set = set(available_dates)
    window_start, window_end = available_dates[0], available_dates[-1]

    months = []
    y, m = window_start.year, window_start.month
    cal = _calendar.Calendar(firstweekday=6)  # Monday first

    while (y, m) <= (window_end.year, window_end.month):
        weeks = []
        for week in cal.monthdatescalendar(y, m):
            week_days = []
            for d in week:
                week_days.append({
                    "date": d,
                    "day_num": d.day,
                    "in_month": d.month == m,
                    "in_range": window_start <= d <= window_end,
                    "available": d in available_set,
                    "is_today": d == date.today(),
                    "weekend": {5: "sat", 6: "sun"}.get(d.weekday()),
                })
            weeks.append(week_days)
        months.append({"label": date(y, m, 1).strftime("%B %Y"), "year": y, "month": m, "weeks": weeks})
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1

    return months


def shift_month(year, month, delta):
    """Return (year, month) moved by `delta` months (delta may be negative)."""
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


def build_month(year, month, available_dates):
    """
    One Monday-first month grid, for calendars that page month by month with
    prev/next arrows. Only days *inside* the displayed month can be `available`
    (the leading/trailing days from neighbouring months are shown faded and are
    never clickable, so the same date can't be picked from two different pages).
    """
    available_set = set(available_dates)
    cal = _calendar.Calendar(firstweekday=6)
    today = date.today()
    weeks = []
    for week in cal.monthdatescalendar(year, month):
        weeks.append([
            {
                "date": d,
                "day_num": d.day,
                "in_month": d.month == month,
                "available": d.month == month and d in available_set,
                "is_today": d == today,
                "weekend": {5: "sat", 6: "sun"}.get(d.weekday()),
            }
            for d in week
        ])
    return {"year": year, "month": month, "weeks": weeks}
