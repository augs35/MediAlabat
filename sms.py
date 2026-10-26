"""
SMS notifications for appointment approve/reject.

Delivery mode is hardcoded via SMS_PROVIDER_HARDCODED:
  "esp32"      queue in the database for the ESP32 + SIM800L gateway (default)
  "console"    print to the terminal
  "semaphore"  send via Semaphore API (needs SEMAPHORE_API_KEY)

Semaphore is a PH-focused SMS API (https://semaphore.co).
"""

import os
import requests

SEMAPHORE_API_KEY = os.environ.get("SEMAPHORE_API_KEY")
SEMAPHORE_URL = "https://api.semaphore.co/api/v4/messages"
SEMAPHORE_SENDER_NAME = os.environ.get("SEMAPHORE_SENDER_NAME")  # optional, needs approval from Semaphore

# Hardcoded SMS provider — "esp32" routes messages to the database queue
# for the ESP32 + SIM800L gateway. "console" just prints them.
SMS_PROVIDER_HARDCODED = "esp32"


def normalize_ph_number(raw):
    """Turn a locally-entered PH mobile number into 09XXXXXXXXX."""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if digits.startswith("63"):
        digits = "0" + digits[2:]
    elif not digits.startswith("0"):
        digits = "0" + digits
    return digits


def sms_mode():
    """Hardcoded delivery mode — see SMS_PROVIDER_HARDCODED at top of file."""
    return SMS_PROVIDER_HARDCODED


def send_sms(to_number, message):
    """Send (or queue) an SMS according to sms_mode()."""
    if sms_mode() == "esp32":
        from db import get_db
        from sms_gateway import enqueue
        conn = get_db()
        try:
            enqueue(conn, to_number, message)
        finally:
            conn.close()
        return True

    formatted = normalize_ph_number(to_number)

    if sms_mode() == "console" or not SEMAPHORE_API_KEY:
        print(f"[SMS - not sent, console mode] To: {formatted}\n{message}")
        return True

    payload = {
        "apikey": SEMAPHORE_API_KEY,
        "number": formatted,
        "message": message,
    }
    if SEMAPHORE_SENDER_NAME:
        payload["sendername"] = SEMAPHORE_SENDER_NAME

    try:
        resp = requests.post(SEMAPHORE_URL, data=payload, timeout=10)
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"[SMS send failed] To: {formatted} — {e}")
        return False


def appointment_status_message(status, clinic_name, appt_date, appt_time):
    if status == "Approved":
        return (
            f"MediAlabat: Your appointment at {clinic_name} on {appt_date} at {appt_time} "
            f"has been APPROVED. Please arrive 10 minutes early."
        )
    else:
        return (
            f"MediAlabat: Your appointment at {clinic_name} on {appt_date} at {appt_time} "
            f"needs to be RESCHEDULED. Please log in to MediAlabat to pick a different slot."
        )