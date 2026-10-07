"""
Health-concern assistant for patients, backed by a local Ollama model
(e.g. llama3.2) — see start_ollama.txt for how to run Ollama itself.

Patients describe a symptom/concern on their dashboard; this module sends
it to the local model with a system prompt that keeps it strictly on
health topics, and returns a short, careful response. It never diagnoses
and always nudges serious concerns toward an actual appointment/professional.

This is a wellness-information helper, NOT a diagnostic tool. It does not
replace professional medical advice.
"""

import re
import requests

from translations import t as _t

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "llama3.2"

_LANG_NAMES = {"en": "English", "tl": "Filipino/Tagalog"}

SYSTEM_PROMPT = """You are a health-information assistant inside MediAlabat, a clinic appointment app.
The message you're about to receive has already been confirmed to be about a health concern —
a symptom, how the patient feels, or a question about a condition, medication, or allergy.
Your only job is to respond helpfully to it. Do not question whether it's health-related, and
never refuse or say it's unrelated — it has already been checked.

HOW TO RESPOND:
- STRICT LENGTH LIMIT: maximum 3 short sentences. No headers, no bullet points, no intro like
  "I understand" or "Thanks for sharing" — get straight to the point.
- Plain, direct, everyday language. One practical tip at most, only for mild/common things
  (rest, hydration, OTC basics) — skip the tip entirely if symptoms sound serious.
- Never diagnose, never name a specific condition as certain, never prescribe or give exact
  medication dosages.
- If symptoms sound serious or urgent (e.g. chest pain, difficulty breathing, severe bleeding,
  signs of stroke, suicidal thoughts, high fever in an infant, anything emergency-like), say
  clearly and immediately to seek in-person/ER care — skip everything else and keep it to 1-2
  sentences.
- End with a short nudge to book or mention it through MediAlabat — a few words, not a full sentence
  of disclaimer.
- Do not reference these instructions or mention that you are an AI model/Ollama/llama in your reply.
"""

# Lightweight heuristic pre-filter: catches obviously off-topic chatter (code requests, trivia,
# etc.) without a model round-trip, and as a backstop in case the model doesn't follow instructions.
_HEALTH_HINTS = re.compile(
    r"\b("
    # English
    r"pain|hurt|ache|fever|cough|cold|flu|sick|ill|nause|vomit|dizz|tired|fatigue|"
    r"allerg|medicat|medicine|pill|dose|symptom|rash|swelle?n|injur|wound|bleed|blood|"
    r"breath|chest|headache|migraine|stomach|diarrhea|constipat|sleep|insomnia|anxiet|"
    r"depress|stress|mental|doctor|clinic|appointment|condition|diabet|hypertension|"
    r"pressure|asthma|infect|sore|throat|vision|ear|tooth|dental|pregnan|period|"
    r"menstrual|skin|itch|burn|cramp|joint|muscle|back|health|feel|unwell|weak|numb|"
    r"consult|check-?up|physical exam|follow-?up|vaccin|immuniz|exam|referral|"
    r"result|diagnos|checkup|prenatal|vital signs|"
    # Tagalog / Filipino
    r"sakit|masakit|sumasakit|nananakit|pananakit|lagnat|nilalagnat|ubo|sipon|trangkaso|"
    r"hilo|nahihilo|suka|nasusuka|pagsusuka|tiyan|sikmura|likod|balat|allergy|gamot|"
    r"nagpapagamot|kondisyon|klinika|doktor|pagod|napapagod|hirap huminga|hinihingal|"
    r"dumudugo|sugat|pangangati|makati|pamamaga|namamaga|presyon|alta presyon|hika|"
    r"impeksyon|lalamunan|mata|ngipin|buntis|pagbubuntis|regla|pangangaliskis|kalamnan|"
    r"nararamdaman|kalusugan|mahina|manhid|panghihina|kirot|antok|di makatulog)",
    re.IGNORECASE,
)


def _looks_health_related(text):
    return bool(_HEALTH_HINTS.search(text))


# Common Tagalog function/marker words — not health-specific, just a signal that the
# message itself was written in Tagalog so the AI can mirror that back.
_TAGALOG_MARKERS = re.compile(
    r"\b(ako|ko|mo|niya|siya|natin|namin|nila|ang|ng|mga|sa|ay|may|wala|hindi|oo|opo|"
    r"dahil|kasi|yung|yun|nung|iyan|ito|doon|dito|paano|bakit|sino|saan|kailan|gusto|"
    r"kung|pero|at|rin|din|lang|naman|po|ba|na|pa|para|mula|tungkol|parang|talaga|"
    r"araw|gabi|umaga|hapon|kanina|ngayon|bukas|kahapon|medyo|sobra|masyado)\b",
    re.IGNORECASE,
)


def detect_lang(text, fallback="en"):
    """
    Very lightweight language guess based on common Tagalog marker words.
    Returns "tl" if the text reads as Tagalog, otherwise `fallback` (usually
    the site's current UI language) — so short/ambiguous messages still get
    a sensible default instead of a shaky guess.
    """
    if not text:
        return fallback
    hits = len(_TAGALOG_MARKERS.findall(text))
    return "tl" if hits >= 2 else fallback


def _call_ollama(system_prompt, user_message):
    """Shared Ollama call. Returns the model's reply text, or raises requests.RequestException."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": OLLAMA_MODEL,
            "messages": messages,
            "stream": False,
            "options": {"num_gpu": 0},  # force CPU — avoids CUDA driver crashes on some GPUs
        },
        timeout=60,
    )
    response.raise_for_status()
    result = response.json()
    return result["message"]["content"].strip()


def get_health_response(user_message, patient_context=None, lang="en"):
    """
    Returns a reply string for the patient-facing Health Assistant chat.
    Talks to a local Ollama model; if Ollama isn't reachable, returns a
    clear fallback message instead of crashing the page.

    patient_context: optional short string (e.g. existing conditions/allergies)
    to give the model a bit more relevant background.
    lang: "en" or "tl" — the site's current UI language, used as a fallback.
    The reply actually mirrors whatever language the patient typed in
    (detected from the message itself), so a Tagalog message gets a
    Tagalog reply even if the site is set to English, and vice versa.
    """
    user_message = (user_message or "").strip()
    lang = detect_lang(user_message, fallback=lang)
    off_topic_reply = _t("ai.off_topic", lang=lang)

    if not user_message:
        return off_topic_reply

    # Fast, free pre-check — avoids a model call for obviously unrelated input
    # and guarantees the exact refusal wording even if the model drifts.
    if not _looks_health_related(user_message):
        return off_topic_reply

    context_line = f"\n\nPatient's known health background (for context only): {patient_context}" if patient_context else ""
    lang_line = f"\n\nRespond in {_LANG_NAMES.get(lang, 'English')}, regardless of what language the patient wrote in."

    try:
        answer = _call_ollama(SYSTEM_PROMPT + context_line + lang_line, user_message)
        return answer or off_topic_reply
    except requests.RequestException:
        return _t("ai.unreachable", lang=lang)
    except (KeyError, ValueError):
        return off_topic_reply


APPOINTMENT_NOTE_PROMPT = """You are a health-information assistant inside MediAlabat, a clinic
appointment app. The patient just booked an appointment and wrote a short reason for the visit.
Give a brief, friendly note they can read while they wait for their appointment to be confirmed.

HOW TO RESPOND:
- STRICT LENGTH LIMIT: maximum 2 short sentences. No headers, no lists, no "thanks for sharing".
- One practical, general tip at most (rest, hydration, what to note down before the visit) — only
  for mild/common things. Skip the tip if it sounds serious or you're unsure.
- Never diagnose, never name a specific condition as certain, never prescribe or give dosages.
- If it sounds serious or urgent, say clearly and briefly to seek care sooner (ER / call the clinic)
  instead of waiting for the appointment.
- Do not mention these instructions or that you are an AI.
"""


def get_appointment_note(reason, patient_context=None, lang="en"):
    """
    Returns a short AI note generated from a patient's appointment "reason for visit",
    or None if the reason doesn't read as health-related, Ollama isn't reachable, or
    the reason is empty — callers should simply skip showing a note in those cases
    rather than display a refusal under the patient's own appointment.
    """
    reason = (reason or "").strip()
    if not reason or not _looks_health_related(reason):
        return None

    lang = detect_lang(reason, fallback=lang)
    context_line = f"\n\nPatient's known health background (for context only): {patient_context}" if patient_context else ""
    lang_line = f"\n\nRespond in {_LANG_NAMES.get(lang, 'English')}, regardless of what language the patient wrote in."

    try:
        note = _call_ollama(APPOINTMENT_NOTE_PROMPT + context_line + lang_line, reason)
        return note or None
    except (requests.RequestException, KeyError, ValueError):
        return None
