"""In-app notifications for patients: 'your appointment was approved / needs rescheduling', shown as toasts."""


def create(conn, user_id, kind, appointment_id):
    conn.execute(
        "INSERT INTO notifications (user_id, kind, appointment_id) VALUES (?, ?, ?)",
        (user_id, kind, appointment_id),
    )
    conn.commit()


def take_unseen(conn, user_id, limit=5):
    """The newest `limit` unseen notifications (oldest first), all unseen ones marked seen."""
    rows = conn.execute(
        "SELECT n.id, n.kind, a.appt_date, a.appt_time, c.name AS clinic_name "
        "FROM notifications n "
        "LEFT JOIN appointments a ON a.id = n.appointment_id "
        "LEFT JOIN clinics c ON c.id = a.clinic_id "
        "WHERE n.user_id = ? AND n.seen = 0 ORDER BY n.id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    conn.execute("UPDATE notifications SET seen = 1 WHERE user_id = ? AND seen = 0", (user_id,))
    conn.commit()
    return list(reversed(rows))
