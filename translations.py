"""
Lightweight i18n for MediAlabat — no extra dependency, just a lookup dict
and a `t()` helper injected into every template via a Flask context
processor (see app.py).

Usage in templates:   {{ t('nav.dashboard') }}
Usage in Python:      from translations import t; t('flash.welcome')
"""

DEFAULT_LANG = "en"
SUPPORTED_LANGS = ("en", "tl")

TRANSLATIONS = {
    # ---------- Navigation ----------
    "nav.dashboard": {"en": "Dashboard", "tl": "Dashboard"},
    "nav.health_assistant": {"en": "Health Assistant", "tl": "Katulong sa Kalusugan"},
    "nav.set_appointment": {"en": "Set Appointment", "tl": "Mag-set ng Appointment"},
    "nav.my_appointments": {"en": "My Appointments", "tl": "Aking mga Appointment"},
    "nav.my_profile": {"en": "My Profile", "tl": "Aking Profile"},
    "nav.requests": {"en": "Requests", "tl": "Mga Request"},
    "nav.my_schedule": {"en": "My Schedule", "tl": "Aking Schedule"},
    "nav.logout": {"en": "Log out", "tl": "Mag-logout"},
    "nav.back": {"en": "Back", "tl": "Bumalik"},

    # ---------- Auth ----------
    "auth.tagline": {"en": "Your health, within reach.", "tl": "Ang kalusugan mo, abot-kamay."},
    "auth.signup_title": {"en": "Create your patient account", "tl": "Gumawa ng patient account"},
    "auth.login_title": {"en": "Log in to your account", "tl": "Mag-login sa iyong account"},
    "auth.full_name": {"en": "Full Name", "tl": "Buong Pangalan"},
    "auth.email": {"en": "Email / Username", "tl": "Email / Username"},
    "auth.email_only": {"en": "Email", "tl": "Email"},
    "auth.password": {"en": "Password", "tl": "Password"},
    "auth.contact_number": {"en": "Contact Number", "tl": "Numero ng Telepono"},
    "auth.dob": {"en": "Date of Birth", "tl": "Petsa ng Kapanganakan"},
    "auth.sex": {"en": "Sex", "tl": "Kasarian"},
    "auth.sex_select": {"en": "Select", "tl": "Pumili"},
    "auth.sex_female": {"en": "Female", "tl": "Babae"},
    "auth.sex_male": {"en": "Male", "tl": "Lalaki"},
    "auth.sex_prefer_not": {"en": "Prefer not to say", "tl": "Ayaw sabihin"},
    "auth.address": {"en": "Address", "tl": "Address"},
    "auth.emergency_contact": {"en": "Emergency Contact (name & number)", "tl": "Emergency Contact (pangalan at numero)"},
    "profile.emergency_contact": {"en": "Emergency Contact", "tl": "Emergency Contact"},
    "auth.create_account": {"en": "Create Account", "tl": "Gumawa ng Account"},
    "auth.log_in": {"en": "Log In", "tl": "Mag-login"},
    "auth.have_account": {"en": "Already have an account?", "tl": "May account ka na ba?"},
    "auth.new_here": {"en": "New here?", "tl": "Bago ka dito?"},
    "auth.create_patient_account": {"en": "Create a patient account", "tl": "Gumawa ng patient account"},
    "auth.staff_demo": {"en": "Staff demo login:", "tl": "Staff demo login:"},
    "auth.staff_signup_title": {"en": "Create a doctor/staff account", "tl": "Gumawa ng doctor/staff account"},
    "auth.are_you_staff": {"en": "Are you a doctor or clinic staff?", "tl": "Doktor o staff ka ba ng klinika?"},
    "auth.create_staff_account": {"en": "Create a staff account", "tl": "Gumawa ng staff account"},
    "auth.are_you_patient": {"en": "Are you a patient?", "tl": "Pasyente ka ba?"},
    "auth.back_to_patient_signup": {"en": "Patient sign up", "tl": "Patient sign up"},
    "auth.clinic_optional": {"en": "Clinic (can be set later)", "tl": "Klinika (puwedeng itakda mamaya)"},
    "auth.clinic_select": {"en": "Select a clinic", "tl": "Pumili ng klinika"},
    "auth.specialization": {"en": "Specialization", "tl": "Espesyalisasyon"},
    "auth.signup_success": {"en": "Account created! Please log in.", "tl": "Nagawa na ang account! Mag-login ka na."},
    "auth.email_exists": {"en": "An account with that email already exists.", "tl": "May account na gamit ang email na iyan."},
    "auth.login_failed": {"en": "Incorrect email or password.", "tl": "Maling email o password."},

    # ---------- Patient Dashboard ----------
    "dash.welcome": {"en": "Welcome", "tl": "Maligayang pagdating"},
    "dash.upcoming_appt": {"en": "Upcoming Appointment", "tl": "Paparating na Appointment"},
    "dash.no_upcoming": {"en": "No upcoming appointments", "tl": "Walang paparating na appointment"},
    "dash.ready": {"en": "Ready to see a doctor? Book a slot in a few taps.", "tl": "Handa nang magpatingin? Mag-book sa ilang tap lang."},
    "dash.status": {"en": "Status", "tl": "Status"},
    "dash.not_feeling_well": {"en": "Not feeling well?", "tl": "May nararamdaman ka ba?"},
    "dash.ask_ai": {"en": "Tell the Health Assistant what's going on for quick guidance on what to do next.", "tl": "Sabihin sa Katulong sa Kalusugan ang nararamdaman mo para sa mabilisang payo."},
    "dash.ask_ai_btn": {"en": "Ask the Health Assistant", "tl": "Magtanong sa Katulong sa Kalusugan"},
    "dash.set_appointment": {"en": "Set Appointment", "tl": "Mag-set ng Appointment"},
    "dash.view_appointments": {"en": "View Appointments", "tl": "Tingnan ang mga Appointment"},

    # ---------- Patient Profile ----------
    "profile.title": {"en": "My Profile", "tl": "Aking Profile"},
    "profile.personal_info": {"en": "Personal Information", "tl": "Personal na Impormasyon"},
    "profile.health_info": {"en": "Health Information", "tl": "Impormasyon sa Kalusugan"},
    "profile.existing_conditions": {"en": "Existing Medical Conditions", "tl": "Kasalukuyang Kondisyong Medikal"},
    "profile.allergies": {"en": "Allergies", "tl": "Allergy"},
    "profile.medications": {"en": "Current Medications", "tl": "Kasalukuyang Gamot"},
    "profile.medical_history": {"en": "Relevant Medical History", "tl": "Kasaysayang Medikal"},
    "profile.none_reported": {"en": "None reported", "tl": "Wala pang naitala"},
    "profile.edit_btn": {"en": "Edit Health Info", "tl": "I-edit ang Impormasyon sa Kalusugan"},
    "profile.edit_title": {"en": "Edit Profile", "tl": "I-edit ang Profile"},
    "profile.personal": {"en": "Personal", "tl": "Personal"},
    "profile.save_changes": {"en": "Save Changes", "tl": "I-save ang mga Pagbabago"},
    "profile.updated": {"en": "Profile updated.", "tl": "Na-update na ang profile."},

    # ---------- Appointment wizard ----------
    "wizard.step_clinic": {"en": "Clinic", "tl": "Klinika"},
    "wizard.step_datetime": {"en": "Date & Time", "tl": "Petsa at Oras"},
    "wizard.step_reason": {"en": "Reason", "tl": "Dahilan"},
    "wizard.step_confirm": {"en": "Confirm", "tl": "Kumpirmahin"},
    "wizard.select_clinic": {"en": "Select Clinic", "tl": "Pumili ng Klinika"},
    "wizard.continue": {"en": "Continue", "tl": "Magpatuloy"},
    "wizard.select_datetime": {"en": "Select Date & Time", "tl": "Pumili ng Petsa at Oras"},
    "wizard.no_doctor": {"en": "This clinic has no assigned doctor yet. Please choose a different clinic.", "tl": "Wala pang doktor ang klinikang ito. Pumili ng ibang klinika."},
    "wizard.back_to_clinics": {"en": "← Back to Clinics", "tl": "← Bumalik sa mga Klinika"},
    "wizard.available_dates": {"en": "Available Dates", "tl": "Mga Bukas na Petsa"},
    "wizard.no_dates": {"en": "No available dates in the next two weeks for this clinic.", "tl": "Walang available na petsa sa susunod na dalawang linggo para sa klinikang ito."},
    "wizard.available_time": {"en": "Available Time", "tl": "Mga Bukas na Oras"},
    "wizard.booked": {"en": "Booked", "tl": "Nabooking na"},
    "wizard.reason_title": {"en": "Reason for Appointment", "tl": "Dahilan ng Appointment"},
    "wizard.reason_label": {"en": "What is this visit for?", "tl": "Para saan ang pagbisitang ito?"},
    "wizard.reason_placeholder": {"en": "e.g. General consultation, follow-up check-up...", "tl": "hal. General consultation, follow-up check-up..."},
    "wizard.summary_title": {"en": "Appointment Summary", "tl": "Buod ng Appointment"},
    "wizard.patient": {"en": "Patient", "tl": "Pasyente"},
    "wizard.clinic": {"en": "Clinic", "tl": "Klinika"},
    "wizard.doctor": {"en": "Doctor", "tl": "Doktor"},
    "wizard.date": {"en": "Date", "tl": "Petsa"},
    "wizard.time": {"en": "Time", "tl": "Oras"},
    "wizard.reason_for_appt": {"en": "Reason for Appointment", "tl": "Dahilan ng Appointment"},
    "wizard.health_on_file": {"en": "Health Information on File", "tl": "Nakatalang Impormasyon sa Kalusugan"},
    "wizard.submit": {"en": "Submit Appointment", "tl": "I-submit ang Appointment"},
    "wizard.cancel": {"en": "Cancel", "tl": "Kanselahin"},
    "wizard.submitted": {"en": "Appointment submitted! You'll be notified once it's reviewed.", "tl": "Naisumite na ang appointment! Mapapabatid ka kapag na-review na ito."},
    "wizard.select_time": {"en": "Select a time", "tl": "Pumili ng oras"},
    "wizard.no_times": {"en": "No available times for this date.", "tl": "Walang available na oras sa petsang ito."},
    "wizard.pick_a_date": {"en": "Pick a date on the calendar above.", "tl": "Pumili ng petsa sa kalendaryo sa taas."},
    "wizard.morning": {"en": "Morning", "tl": "Umaga"},
    "wizard.afternoon": {"en": "Afternoon", "tl": "Hapon"},

    # ---------- Staff slot management ----------
    "staff.manage_dates": {"en": "Manage Specific Dates", "tl": "Pamahalaan ang mga Partikular na Petsa"},
    "staff.manage_dates_sub": {"en": "Pick a date to mark individual time slots as fully booked — patients won't be able to request those times. You decide, not the system.",
                                "tl": "Pumili ng petsa para markahan ang mga oras bilang fully booked — hindi na makaka-request ang pasyente ng oras na iyon. Ikaw ang magpapasya, hindi ang sistema."},
    "staff.no_manageable_dates": {"en": "Set a weekly schedule below first — then you can manage specific dates here.", "tl": "Magtakda muna ng lingguhang schedule sa ibaba — pagkatapos ay puwede ka nang mamahala ng partikular na petsa dito."},
    "staff.slot_open": {"en": "Open", "tl": "Bukas"},
    "staff.slot_blocked": {"en": "Fully Booked", "tl": "Fully Booked"},
    "staff.slot_approved": {"en": "Booked (Approved)", "tl": "Naka-book (Aprubado)"},
    "staff.slot_past": {"en": "Past", "tl": "Nakalipas na"},
    "staff.mark_full": {"en": "Mark Full", "tl": "I-mark na Puno"},
    "staff.reopen": {"en": "Reopen", "tl": "Buksan Muli"},

    # ---------- Appointments list ----------
    "appts.title": {"en": "My Appointments", "tl": "Aking mga Appointment"},
    "appts.none_yet": {"en": "You haven't booked any appointments yet.", "tl": "Wala ka pang nabo-book na appointment."},
    "status.pending": {"en": "Pending", "tl": "Naghihintay"},
    "status.approved": {"en": "Approved", "tl": "Aprubado"},
    "status.reschedule": {"en": "Reschedule", "tl": "I-reschedule"},
    "appts.notified_sms": {"en": "Appointment {status}. Patient notified by SMS.", "tl": "Appointment {status}. Na-text na ang pasyente."},

    # ---------- Health Assistant ----------
    "ai.title": {"en": "Health Assistant", "tl": "Katulong sa Kalusugan"},
    "ai.subtitle": {"en": "Describe a symptom or health concern and get general guidance. This is not a diagnosis — for anything serious or ongoing, please book an appointment.",
                     "tl": "Ilarawan ang sintomas o alalahanin sa kalusugan para sa pangkalahatang payo. Hindi ito diagnosis — kung seryoso o tuluy-tuloy, mag-book ng appointment."},
    "ai.placeholder": {"en": "Describe what you're feeling or a health question you have...", "tl": "Ilarawan ang nararamdaman mo o ang tanong mo tungkol sa kalusugan..."},
    "ai.send": {"en": "Send", "tl": "Ipadala"},
    "ai.empty": {"en": 'No messages yet. Try something like "I\'ve had a mild headache since this morning" below.',
                 "tl": 'Wala pang mensahe. Subukan ang katulad ng "May banayad akong sakit ng ulo simula kaninang umaga" sa ibaba.'},
    "ai.clear": {"en": "Clear conversation", "tl": "Burahin ang usapan"},
    "ai.whats_going_on": {"en": "What's going on?", "tl": "Ano ang nararamdaman mo?"},
    "ai.off_topic": {"en": "Sorry, that's unrelated to a health concern. Please try again.",
                      "tl": "Paumanhin, hindi ito tungkol sa alalahanin sa kalusugan. Subukan ulit."},
    "ai.unreachable": {"en": "The health assistant isn't reachable right now. Please try again in a moment, or reach out to your clinic directly.",
                        "tl": "Hindi muna ma-reach ang Health Assistant. Subukan ulit mamaya, o direktang tumawag sa iyong klinika."},

    # ---------- Staff ----------
    "staff.welcome": {"en": "Welcome", "tl": "Maligayang pagdating"},
    "staff.pending_appts": {"en": "Pending Appointments", "tl": "Mga Naghihintay na Appointment"},
    "staff.review_requests": {"en": "Review Requests", "tl": "I-review ang mga Request"},
    "staff.today_appts": {"en": "Today's Appointments", "tl": "Mga Appointment Ngayong Araw"},
    "staff.no_today": {"en": "No appointments scheduled for today.", "tl": "Walang naka-schedule na appointment ngayong araw."},
    "staff.requests_title": {"en": "Appointment Requests", "tl": "Mga Kahilingan ng Appointment"},
    "staff.no_pending": {"en": "No pending requests right now.", "tl": "Walang kasalukuyang naghihintay na kahilingan."},
    "staff.view_application": {"en": "View Application", "tl": "Tingnan ang Application"},
    "staff.details_title": {"en": "Appointment Details", "tl": "Detalye ng Appointment"},
    "staff.patient_info": {"en": "Patient Information", "tl": "Impormasyon ng Pasyente"},
    "staff.name": {"en": "Name", "tl": "Pangalan"},
    "staff.contact": {"en": "Contact", "tl": "Contact"},
    "staff.dob": {"en": "Date of Birth", "tl": "Petsa ng Kapanganakan"},
    "staff.sex": {"en": "Sex", "tl": "Kasarian"},
    "staff.conditions": {"en": "Existing Conditions", "tl": "Kasalukuyang Kondisyon"},
    "staff.allergies": {"en": "Allergies", "tl": "Allergy"},
    "staff.medications": {"en": "Medications", "tl": "Gamot"},
    "staff.history": {"en": "Medical History", "tl": "Kasaysayang Medikal"},
    "staff.appointment": {"en": "Appointment", "tl": "Appointment"},
    "staff.requested_date": {"en": "Requested Date", "tl": "Hiniling na Petsa"},
    "staff.requested_time": {"en": "Requested Time", "tl": "Hiniling na Oras"},
    "staff.reason": {"en": "Reason", "tl": "Dahilan"},
    "staff.status": {"en": "Status", "tl": "Status"},
    "staff.approve": {"en": "Approve", "tl": "Aprubahan"},
    "staff.reschedule": {"en": "Reschedule", "tl": "I-reschedule"},
    "staff.my_schedule": {"en": "My Schedule", "tl": "Aking Schedule"},
    "staff.schedule_sub": {"en": "Set your weekly availability. MediAlabat automatically turns this into bookable slots for patients.",
                            "tl": "Itakda ang iyong lingguhang availability. Awtomatikong gagawin ito ng MediAlabat na mga puwedeng i-book ng pasyente."},
    "staff.unavailable": {"en": "Unavailable", "tl": "Hindi Available"},
    "staff.add_schedule": {"en": "+ Add Schedule Block", "tl": "+ Magdagdag ng Schedule"},
    "staff.day": {"en": "Day", "tl": "Araw"},
    "staff.start_time": {"en": "Start Time", "tl": "Oras ng Simula"},
    "staff.end_time": {"en": "End Time", "tl": "Oras ng Pagtatapos"},
    "staff.add_schedule_btn": {"en": "Add Schedule", "tl": "Idagdag ang Schedule"},
    "staff.remove": {"en": "Remove", "tl": "Alisin"},
    "staff.schedule_added": {"en": "Schedule block added.", "tl": "Naidagdag na ang schedule."},
    "staff.profile_title": {"en": "My Profile", "tl": "Aking Profile"},
    "staff.specialization": {"en": "Specialization", "tl": "Espesyalisasyon"},
    "staff.clinic": {"en": "Clinic", "tl": "Klinika"},
    "staff.manage_schedule_link": {"en": "Manage available schedule →", "tl": "Pamahalaan ang schedule →"},

    # Weekday names
    "day.monday": {"en": "Monday", "tl": "Lunes"},
    "day.tuesday": {"en": "Tuesday", "tl": "Martes"},
    "day.wednesday": {"en": "Wednesday", "tl": "Miyerkules"},
    "day.thursday": {"en": "Thursday", "tl": "Huwebes"},
    "day.friday": {"en": "Friday", "tl": "Biyernes"},
    "day.saturday": {"en": "Saturday", "tl": "Sabado"},
    "day.sunday": {"en": "Sunday", "tl": "Linggo"},
}


def t(key, lang="en", **kwargs):
    """Look up a translation key for the given language, with safe fallback to English, then the key itself."""
    entry = TRANSLATIONS.get(key)
    if not entry:
        return key
    text = entry.get(lang) or entry.get(DEFAULT_LANG) or key
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError):
            pass
    return text
