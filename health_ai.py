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

# Lightweight heuristic pre-filter:
# Catches obviously health/medical-related messages without a model round-trip.
# This acts as an early classifier and a backstop in case the model doesn't
# follow the intended health-only instructions.

_HEALTH_HINTS = re.compile(
    r"\b("
    # ============================================================
    # ENGLISH — GENERAL HEALTH / SYMPTOMS
    # ============================================================
    r"pain|painful|hurt|hurting|ache|aching|sore|"
    r"fever|febrile|temperature|chills|shiver|"
    r"cough|coughing|cold|flu|influenza|"
    r"sick|ill|unwell|nausea|nauseous|vomit|vomiting|"
    r"dizzy|dizziness|vertigo|faint|fainting|"
    r"tired|tiredness|fatigue|weak|weakness|"
    r"headache|migraine|"
    r"rash|itch|itching|swelling|swollen|"
    r"bleed|bleeding|blood|bruise|bruising|"
    r"wound|injury|injured|burn|burned|"
    r"breath|breathing|shortness of breath|"
    r"chest|palpitation|palpitations|heartbeat|"
    r"stomach|abdomen|abdominal|belly|"
    r"diarrhea|constipation|constipated|"
    r"vomit|vomiting|"
    r"cramp|cramps|"
    r"joint|joints|muscle|muscles|"
    r"back pain|neck pain|shoulder|"
    r"numb|numbness|tingling|"
    r"vision|blurry|blurred|blind|"
    r"ear|hearing|ringing|tinnitus|"
    r"tooth|teeth|toothache|dental|gum|"
    r"throat|sore throat|"
    r"skin|acne|pimple|eczema|"
    r"hair loss|hairloss|"
    r"sleep|insomnia|sleeping|"
    r"appetite|hungry|"
    r"dehydrated|dehydration|thirsty|"
    
    # ============================================================
    # ENGLISH — CONDITIONS / DISEASES
    # ============================================================
    r"allerg|allergic|"
    r"asthma|"
    r"diabet|"
    r"hypertension|hypotension|"
    r"high blood pressure|low blood pressure|"
    r"high cholesterol|cholesterol|"
    r"heart disease|heart attack|"
    r"stroke|"
    r"cancer|tumor|tumour|"
    r"anemia|anaemia|"
    r"arthritis|"
    r"migraine|"
    r"ulcer|"
    r"gastritis|"
    r"acid reflux|reflux|gerd|"
    r"kidney|kidney stone|"
    r"liver|"
    r"thyroid|"
    r"pneumonia|"
    r"tuberculosis|tb|"
    r"infection|infected|infectious|"
    r"covid|coronavirus|"
    r"dengue|"
    r"malaria|"
    
    # ============================================================
    # ENGLISH — MEDICATION / TREATMENT
    # ============================================================
    r"medication|medicine|meds|drug|drugs|"
    r"pill|pills|tablet|tablets|capsule|capsules|"
    r"syrup|ointment|cream|inhaler|"
    r"antibiotic|antibiotics|"
    r"painkiller|painkillers|"
    r"paracetamol|acetaminophen|ibuprofen|aspirin|"
    r"amoxicillin|"
    r"dose|dosage|overdose|"
    r"prescription|prescribed|"
    r"side effect|side effects|"
    r"treatment|therapy|"
    r"vaccine|vaccination|vaccinated|vaccin|"
    r"immunization|immuniz|"
    r"first aid|"
    r"bandage|band-aid|"
    
    # ============================================================
    # ENGLISH — MEDICAL SERVICES / PROFESSIONALS
    # ============================================================
    r"doctor|physician|nurse|nursing|"
    r"dentist|dentistry|"
    r"pharmacist|pharmacy|"
    r"therapist|psychologist|psychiatrist|"
    r"clinic|hospital|health center|healthcare|"
    r"medical|medicine|"
    r"appointment|consultation|consult|"
    r"check-?up|checkup|"
    r"physical exam|medical exam|"
    r"follow-?up|"
    r"referral|"
    r"emergency room|emergency department|"
    r"ambulance|"
    
    # ============================================================
    # ENGLISH — TESTS / RESULTS / DIAGNOSIS
    # ============================================================
    r"symptom|symptoms|"
    r"diagnos|diagnosis|diagnosed|"
    r"test result|test results|"
    r"lab result|lab results|"
    r"laboratory|lab test|"
    r"blood test|blood work|"
    r"blood sugar|blood glucose|glucose|"
    r"blood pressure|bp|"
    r"urinalysis|urine test|"
    r"pregnancy test|"
    r"x-ray|xray|"
    r"ultrasound|"
    r"ct scan|mri|"
    r"ecg|ekg|"
    r"scan|biopsy|"
    r"vital signs|pulse|heart rate|oxygen level|"
    r"oxygen saturation|spo2|"
    
    # ============================================================
    # ENGLISH — MENTAL HEALTH
    # ============================================================
    r"anxiet|anxious|panic|panic attack|"
    r"depress|depression|depressed|"
    r"stress|stressed|"
    r"mental health|mental illness|"
    r"trauma|ptsd|"
    r"ocd|adhd|"
    r"mood|mood swing|"
    r"lonely|loneliness|"
    r"suicid|self-harm|self harm|"
    
    # ============================================================
    # ENGLISH — WOMEN'S / REPRODUCTIVE HEALTH
    # ============================================================
    r"pregnan|pregnancy|pregnant|"
    r"prenatal|postnatal|postpartum|"
    r"period|periods|menstrual|menstruation|"
    r"menopause|"
    r"ovulation|"
    r"fertility|infertility|"
    r"contraception|contraceptive|"
    r"birth control|"
    r"miscarriage|"
    r"breastfeeding|breast feed|"
    r"breast pain|breast lump|"
    
    # ============================================================
    # ENGLISH — EMERGENCY / RED FLAG TERMS
    # ============================================================
    r"emergency|"
    r"unconscious|"
    r"not breathing|"
    r"can't breathe|cannot breathe|"
    r"severe pain|"
    r"heavy bleeding|"
    r"blood clot|"
    r"seizure|seizing|convulsion|"
    r"stroke symptoms|"
    r"heart attack|"
    r"poisoning|poisoned|"
    r"overdose|"
    
    # ============================================================
    # TAGALOG / FILIPINO — GENERAL SYMPTOMS
    # ============================================================
    r"sakit|masakit|sumasakit|nananakit|pananakit|"
    r"kirot|kumíkirot|"
    r"lagnat|nilalagnat|"
    r"giniginaw|panginginig|"
    r"ubo|umuubo|"
    r"sipon|"
    r"trangkaso|"
    r"hilo|nahihilo|pagkahilo|"
    r"himatay|nahimatay|"
    r"suka|nasusuka|pagsusuka|isinusuka|"
    r"pagod|napapagod|pagkapagod|"
    r"panghihina|mahina|"
    r"pangangalay|"
    r"hirap huminga|nahihirapang huminga|"
    r"hinihingal|hingal|"
    r"pananakit ng dibdib|masakit ang dibdib|"
    r"ulo|masakit ang ulo|"
    r"tiyan|masakit ang tiyan|"
    r"sikmura|"
    r"likod|masakit ang likod|"
    r"balat|"
    r"pangangati|makati|"
    r"pamamaga|namamaga|"
    r"pasa|"
    r"sugat|"
    r"pagdurugo|dumudugo|"
    r"manhid|pamamanhid|"
    r"pangangaliskis|"
    r"pangingilo|"
    
    # ============================================================
    # TAGALOG / FILIPINO — CONDITIONS
    # ============================================================
    r"allergy|allergic|"
    r"hika|"
    r"diabetes|diabetis|"
    r"alta presyon|mataas na presyon|"
    r"mababang presyon|"
    r"kolesterol|"
    r"anemia|"
    r"rayuma|"
    r"impeksyon|impeksiyon|"
    r"trangkaso|"
    r"dengue|"
    r"tb|tuberculosis|"
    r"kanser|cancer|"
    r"bato sa bato|kidney stone|"
    
    # ============================================================
    # TAGALOG / FILIPINO — MEDICINE / TREATMENT
    # ============================================================
    r"gamot|mga gamot|"
    r"tableta|kapsula|"
    r"pampawala ng sakit|"
    r"reseta|"
    r"resitahan|"
    r"dosage|dosis|"
    r"iniinom na gamot|"
    r"pamahid|"
    r"bakuna|nagpabakuna|"
    r"pagbabakuna|"
    r"first aid|unang lunas|"
    
    # ============================================================
    # TAGALOG / FILIPINO — MEDICAL SERVICES
    # ============================================================
    r"doktor|doctor|"
    r"nars|nurse|"
    r"dentista|"
    r"parmasyutiko|pharmacist|"
    r"ospital|hospital|"
    r"klinika|clinic|"
    r"health center|sentro ng kalusugan|"
    r"pagamutan|"
    r"nagpapagamot|"
    r"pagpapagamot|"
    r"pagpapatingin|"
    r"magpatingin|"
    r"patingin|"
    r"checkup|check-?up|"
    r"konsultasyon|"
    
    # ============================================================
    # TAGALOG / FILIPINO — TESTS / RESULTS
    # ============================================================
    r"resulta|result|"
    r"resulta ng test|"
    r"pagsusuri|"
    r"laboratoryo|"
    r"blood test|"
    r"asukal sa dugo|"
    r"presyon ng dugo|"
    r"urine test|"
    r"x-ray|xray|"
    r"ultrasound|"
    
    # ============================================================
    # TAGALOG / FILIPINO — MENTAL HEALTH
    # ============================================================
    r"pagkabalisa|balisa|"
    r"panic attack|"
    r"depresyon|depressed|"
    r"stress|nai-stress|"
    r"kalusugan ng isip|"
    r"trauma|"
    r"malungkot|kalungkutan|"
    r"pagod sa isip|"
    
    # ============================================================
    # TAGALOG / FILIPINO — REPRODUCTIVE HEALTH
    # ============================================================
    r"buntis|pagbubuntis|"
    r"nagbubuntis|"
    r"regla|menstruasyon|"
    r"period|"
    r"menopause|"
    r"obulasyon|"
    r"pagpapasuso|"
    r"pagkalaglag|"
    r"kontraseptibo|"
    r"birth control|"
    
    # ============================================================
    # COMMON BODY PARTS
    # ============================================================
    r"head|ulo|"
    r"brain|utak|"
    r"eye|eyes|mata|"
    r"ear|ears|tainga|"
    r"nose|ilong|"
    r"mouth|bibig|"
    r"tongue|dila|"
    r"tooth|teeth|ngipin|"
    r"throat|lalamunan|"
    r"neck|leeg|"
    r"chest|dibdib|"
    r"heart|puso|"
    r"lung|lungs|baga|"
    r"stomach|tiyan|sikmura|"
    r"intestine|bituka|"
    r"kidney|bato|"
    r"liver|atay|"
    r"skin|balat|"
    r"arm|braso|"
    r"hand|kamay|"
    r"finger|daliri|"
    r"leg|binti|"
    r"knee|tuhod|"
    r"foot|paa|"
    r"muscle|kalamnan|"
    r"bone|buto|"
    
    # ============================================================
    # GENERAL HEALTH QUESTIONS
    # ============================================================
    r"am i healthy|"
    r"is this normal|"
    r"should i see a doctor|"
    r"do i need a doctor|"
    r"what should i do|"
    r"what could cause|"
    r"what causes|"
    r"health advice|"
    r"medical advice|"
    r"health concern|"
    r"medical concern|"
    r"symptom checker|"
    
    # Filipino equivalents
    r"normal ba|"
    r"okay lang ba|"
    r"ano ang gagawin|"
    r"anong gagawin|"
    r"ano kaya ito|"
    r"ano kaya ang sanhi|"
    r"ano ang sanhi|"
    r"kailangan ko ba ng doktor|"
    r"dapat ba akong magpatingin|"
    r"may sakit ba ako|"
    r"masama ba ito|"
    r"delikado ba ito|"
    r"tama ba ang gamot|"
    
    # ============================================================
    # ADDITIONS
    # ============================================================
    
    r"swollen|swelling|swell|swelled|puffy|puffiness|edema|oedema|"
    r"pamamaga|namamaga|maga|may pamamaga|may maga"
    
    # Optional plural / -ed / -ing ending, so "headaches", "coughs", "hurts" still match
    # without loosening the word boundary (which would let "ear" match "earth", etc.).
    r")(?:s|es|ed|ing)?\b",
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
