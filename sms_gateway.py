"""
Server side of the ESP32 + SIM800L SMS gateway.

Flow:  staff approve/reschedule  ->  sms.send_sms() -> enqueue()  -> row in sms_outbox (pending)
       ESP32 polls GET /api/sms/next -> claim_next() hands out one message (status 'sending')
       ESP32 sends it through the SIM800L, then POSTs /api/sms/<id>/result -> report_result()

Delivery is "at least once": if the gateway claims a message and then dies before reporting back, the claim
expires after LEASE_SECONDS and the message is handed out again (up to MAX_ATTEMPTS). In that rare case the
patient could get the same text twice — preferable to silently never notifying them.
"""
import re
import unicodedata

LEASE_SECONDS = 180      # how long a claimed message is considered "in flight"
MAX_ATTEMPTS = 3
ONLINE_WINDOW_SECONDS = 60
MAX_SMS_CHARS = 160      # one SMS in GSM 7-bit text mode; the SIM800L does not split longer text for us

_PUNCT = {
    "\u2014": "-", "\u2013": "-", "\u2212": "-", "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u00b7": "-", "\u2022": "-", "\u2026": "...", "\u00a0": " ", "\u2192": "->",
}
# ASCII characters that cost two septets (or map oddly) in the GSM alphabet — not worth the risk in a notification.
_RISKY = str.maketrans({c: " " for c in "[]{}\\^~|`"})


def to_e164_ph(raw):
    """'0917-111-2222' / '+63 917 111 2222' / '639171112222' -> '+639171112222'; None if it isn't a PH mobile number."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("63") and len(digits) == 12:
        digits = "0" + digits[2:]
    elif len(digits) == 10 and digits.startswith("9"):
        digits = "0" + digits
    if re.fullmatch(r"09\d{9}", digits):
        return "+63" + digits[1:]
    return None


def clean_text(message):
    """One line, plain ASCII, at most MAX_SMS_CHARS (so the modem's text mode can send it as a single SMS)."""
    text = message or ""
    for src, dst in _PUNCT.items():
        text = text.replace(src, dst)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.translate(_RISKY)
    text = re.sub(r"\s+", " ", text).strip()          # also removes tabs/newlines (the device protocol is tab-separated)
    if len(text) > MAX_SMS_CHARS:
        text = text[: MAX_SMS_CHARS - 3].rstrip() + "..."
    return text


def enqueue(conn, to_number, message):
    """Queue a message. An unusable number is recorded as 'failed' straight away so staff can see it."""
    number = to_e164_ph(to_number)
    text = clean_text(message)
    if number is None:
        cur = conn.execute(
            "INSERT INTO sms_outbox (to_number, message, status, error) VALUES (?, ?, 'failed', ?)",
            (str(to_number or ""), text, "invalid phone number"),
        )
    else:
        cur = conn.execute("INSERT INTO sms_outbox (to_number, message) VALUES (?, ?)", (number, text))
    conn.commit()
    return cur.lastrowid


def touch_gateway(conn, rssi=None):
    conn.execute("INSERT OR IGNORE INTO sms_gateway (id) VALUES (1)")
    if rssi is None:
        conn.execute("UPDATE sms_gateway SET last_seen = datetime('now') WHERE id = 1")
    else:
        conn.execute("UPDATE sms_gateway SET last_seen = datetime('now'), rssi = ? WHERE id = 1", (rssi,))
    conn.commit()


def claim_next(conn):
    """Hand the oldest deliverable message to the gateway, or None. Safe against two callers racing."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        # Claims that expired with no attempts left are given up on rather than retried forever.
        conn.execute(
            "UPDATE sms_outbox SET status='failed', error='no result from gateway after ' || attempts || ' attempts' "
            "WHERE status='sending' AND attempts >= ? AND claimed_at < datetime('now', ?)",
            (MAX_ATTEMPTS, f"-{LEASE_SECONDS} seconds"),
        )
        row = conn.execute(
            "SELECT * FROM sms_outbox WHERE status='pending' "
            "   OR (status='sending' AND claimed_at < datetime('now', ?)) "
            "ORDER BY id LIMIT 1",
            (f"-{LEASE_SECONDS} seconds",),
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        conn.execute(
            "UPDATE sms_outbox SET status='sending', attempts = attempts + 1, claimed_at = datetime('now') WHERE id = ?",
            (row["id"],),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return conn.execute("SELECT * FROM sms_outbox WHERE id = ?", (row["id"],)).fetchone()


def report_result(conn, sms_id, ok, error=None):
    """Gateway's verdict. Returns False if the id is unknown or wasn't waiting on a result."""
    row = conn.execute("SELECT status, attempts FROM sms_outbox WHERE id = ?", (sms_id,)).fetchone()
    if row is None or row["status"] != "sending":
        return False
    if ok:
        conn.execute("UPDATE sms_outbox SET status='sent', sent_at=datetime('now'), error=NULL WHERE id=?", (sms_id,))
    else:
        reason = (error or "unknown error")[:200]
        final = row["attempts"] >= MAX_ATTEMPTS
        conn.execute(
            "UPDATE sms_outbox SET status=?, error=? WHERE id=?",
            ("failed" if final else "pending", reason, sms_id),
        )
    conn.commit()
    return True


def gateway_status(conn):
    row = conn.execute(
        "SELECT last_seen, rssi, (last_seen IS NOT NULL AND last_seen >= datetime('now', ?)) AS online "
        "FROM sms_gateway WHERE id = 1",
        (f"-{ONLINE_WINDOW_SECONDS} seconds",),
    ).fetchone()
    if row is None:
        return {"online": False, "last_seen": None, "rssi": None}
    return {"online": bool(row["online"]), "last_seen": row["last_seen"], "rssi": row["rssi"]}


def counts(conn):
    out = {"pending": 0, "sending": 0, "sent": 0, "failed": 0}
    for r in conn.execute("SELECT status, COUNT(*) AS n FROM sms_outbox GROUP BY status").fetchall():
        out[r["status"]] = r["n"]
    return out


def recent(conn, limit=25):
    return conn.execute("SELECT * FROM sms_outbox ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
