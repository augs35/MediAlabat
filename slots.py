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

    booked = {
        r["appt_time"]
        for r in conn.execute(
            "SELECT appt_time FROM appointments WHERE staff_id = ? AND appt_date = ? "
            "AND status != 'Rejected'",
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
