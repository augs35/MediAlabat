"""
SMS notifications for appointment approve/reject.

Uses Semaphore (https://semaphore.co) — an SMS API built specifically for
Philippine mobile numbers (Globe, Smart, Sun, DITO). Pay-as-you-go, no
monthly minimum, no "verify the number first" restriction like Twilio's
trial tier.

Credentials are read from an environment variable so nothing is hardcoded:

    SEMAPHORE_API_KEY

If it isn't set, send_sms() just prints the message to the console instead
of failing — so the app still runs fine during local development without a
Semaphore account.
"""

import os
import requests

SEMAPHORE_API_KEY = os.environ.get("SEMAPHORE_API_KEY")
SEMAPHORE_URL = "https://api.semaphore.co/api/v4/messages"
SEMAPHORE_SENDER_NAME = os.environ.get("SEMAPHORE_SENDER_NAME")  # optional, needs approval from Semaphore


def normalize_ph_number(raw):
    """
    Turn a locally-entered PH mobile number into the format Semaphore expects.
    Semaphore accepts 09XXXXXXXXX or 639XXXXXXXXX — we normalize to 09XXXXXXXXX.
    e.g. "0917-111-2222" -> "09171112222", "+639171112222" -> "09171112222"
    """
    digits = "".join(ch for ch in raw if ch.isdigit())
    if digits.startswith("63"):
        digits = "0" + digits[2:]
    elif not digits.startswith("0"):
        digits = "0" + digits
    return digits


def send_sms(to_number, message):
    """
    Send an SMS via Semaphore. Returns True if sent (or simulated), False on
    a real send failure.
    """
    formatted = normalize_ph_number(to_number)

    if not SEMAPHORE_API_KEY:
        # No API key configured — log instead of sending, so local
        # development/testing doesn't require a Semaphore account.
        print(f"[SMS - not sent, no SEMAPHORE_API_KEY configured] To: {formatted}\n{message}")
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
            f"was not approved. Please log in to MediAlabat to book a different slot."
        )
