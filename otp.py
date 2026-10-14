"""
One-time passcodes for doctor/staff accounts: once when an account is created (proves they own the phone number)
and again every time they log in. The code is texted with the normal SMS provider (Semaphore, the ESP32 gateway,
or printed to the terminal in console mode).

Safety rules enforced here:
  - 6 digits from a cryptographic random source; only a salted hash is stored, never the code itself
  - a code works once and expires after 5 minutes; asking for a new one cancels the old one
  - 5 wrong guesses lock that code
  - while a code is still unused, at most one new one every 30 seconds; and 10 codes per hour per account
    (stops text-message spam and brute forcing; a code that was just used never delays the next login)
"""
import hashlib
import hmac
import os
import re
import secrets

from sms_gateway import to_e164_ph

OTP_TTL_SECONDS = 300
MAX_WRONG_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 30
MAX_CODES_PER_HOUR = 10


def otp_required():
    """Set STAFF_OTP_REQUIRED=0 to switch staff verification off (development, or if you've locked yourself out)."""
    return os.environ.get("STAFF_OTP_REQUIRED", "1").strip() != "0"


def mask_phone(raw):
    """'09171112222' -> '0917 *** **22' (so the screen doesn't reveal the whole number)."""
    e164 = to_e164_ph(raw)
    if not e164:
        return ""
    local = "0" + e164[3:]
    return f"{local[:4]} *** **{local[-2:]}"


def _hash(salt, code):
    return hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()


def seconds_until_resend(conn, user_id):
    row = conn.execute(
        "SELECT used, CAST(strftime('%s','now') AS INTEGER) - CAST(strftime('%s', created_at) AS INTEGER) AS age "
        "FROM otp_codes WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    if row is None or row["used"]:       # nothing pending, or the last code was already used successfully
        return 0
    return max(0, RESEND_COOLDOWN_SECONDS - row["age"])


def issue(conn, user_id, purpose, phone):
    """
    Create a fresh code for this account and text it. Returns (status, seconds):
      ("sent", 0)            code created and handed to the SMS provider
      ("cooldown", n)        too soon after the previous code — try again in n seconds
      ("too_many", 0)        hourly limit reached
      ("send_failed", 0)     the SMS provider reported an error (code exists; the user can ask for another)
    """
    wait = seconds_until_resend(conn, user_id)
    if wait > 0:
        return "cooldown", wait
    recent = conn.execute(
        "SELECT COUNT(*) AS n FROM otp_codes WHERE user_id = ? AND created_at > datetime('now', '-1 hour')",
        (user_id,),
    ).fetchone()["n"]
    if recent >= MAX_CODES_PER_HOUR:
        return "too_many", 0

    conn.execute("UPDATE otp_codes SET used = 1 WHERE user_id = ? AND used = 0", (user_id,))  # only the newest code works
    code = f"{secrets.randbelow(10 ** 6):06d}"
    salt = secrets.token_hex(8)
    conn.execute(
        "INSERT INTO otp_codes (user_id, purpose, salt, code_hash, expires_at) "
        "VALUES (?, ?, ?, ?, datetime('now', ?))",
        (user_id, purpose, salt, _hash(salt, code), f"+{OTP_TTL_SECONDS} seconds"),
    )
    conn.commit()

    from sms import send_sms  # imported here so tests can swap the sender
    ok = send_sms(phone, f"MediAlabat: Your verification code is {code}. It expires in 5 minutes. Do not share it.")
    return ("sent" if ok else "send_failed"), 0


def verify(conn, user_id, purpose, submitted):
    """
    Check a submitted code. Returns (status, tries_left):
      "ok" | "wrong" (tries_left says how many remain) | "expired" | "locked" | "none" (no live code)
    """
    row = conn.execute(
        "SELECT *, (expires_at > datetime('now')) AS fresh FROM otp_codes "
        "WHERE user_id = ? AND purpose = ? AND used = 0 ORDER BY id DESC LIMIT 1",
        (user_id, purpose),
    ).fetchone()
    if row is None:
        return "none", 0
    if not row["fresh"]:
        conn.execute("UPDATE otp_codes SET used = 1 WHERE id = ?", (row["id"],))
        conn.commit()
        return "expired", 0
    if row["attempts"] >= MAX_WRONG_ATTEMPTS:
        return "locked", 0

    digits = re.sub(r"\D", "", submitted or "")
    attempts = row["attempts"] + 1
    if len(digits) == 6 and hmac.compare_digest(_hash(row["salt"], digits), row["code_hash"]):
        conn.execute("UPDATE otp_codes SET used = 1, attempts = ? WHERE id = ?", (attempts, row["id"]))
        conn.commit()
        return "ok", 0
    conn.execute("UPDATE otp_codes SET attempts = ? WHERE id = ?", (attempts, row["id"]))
    conn.commit()
    left = MAX_WRONG_ATTEMPTS - attempts
    return ("wrong", left) if left > 0 else ("locked", 0)
