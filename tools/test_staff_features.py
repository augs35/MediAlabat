"""
Checks: staff working at several clinics, Sunday-first schedule/calendars with weekend colours, Morning/Afternoon
slot groups, toast + patient notifications, ending the AI conversation, and the staff nav order.
Uses a throwaway database; never touches your real one.

    python tools/test_staff_features.py
"""
import os, re, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(tempfile.mkdtemp())
os.environ["SECRET_KEY"] = "test-secret"
os.environ["STAFF_OTP_REQUIRED"] = "0"          # passcodes are covered by test_staff_accounts.py

import health_ai                                 # noqa: E402
from app import app                              # noqa: E402
from db import init_db, get_db                   # noqa: E402

init_db()
conn = get_db()
failed = 0
ai_calls = []
health_ai._call_ollama = lambda system_prompt, message: (ai_calls.append(message) or "Rest and drink water.")


def check(cond, label):
    global failed
    failed += 0 if cond else 1
    print(("PASS  " if cond else "FAIL  ") + label)


def staff_client():
    c = app.test_client()
    c.post("/login", data=dict(email="doctor@medialabat.test", password="password123"))
    return c


def signup_patient(email, name="Juan"):
    c = app.test_client()
    c.post("/signup", data=dict(first_name=name, middle_name="", last_name="Cruz", email=email, password="secret123",
           contact_number="0917-111-2222", date_of_birth="2003-05-10", sex="Male", address="x",
           emergency_contact_name="M", emergency_contact_relation="Mother", emergency_contact_number="0918"))
    c.post("/login", data=dict(email=email, password="secret123"))
    return c


doc = staff_client()
doc_id = conn.execute("SELECT id FROM users WHERE role='staff'").fetchone()["id"]

# ================= several clinics =================
page = doc.get("/staff/profile").data.decode()
check(page.count('name="clinic_ids"') == 3 and "<select" not in page.split("clinic_ids")[0].split("Contact")[-1], "profile offers a checkbox per clinic (not a single dropdown)")
check('value="1" checked' in page and 'value="2" checked' not in page, "the clinic the doctor already had is pre-ticked")
doc.post("/staff/profile", data=dict(full_name="Dr. Juan Santos", specialization="GP", contact_number="0917-000-0000", clinic_ids=["1", "2", "3"]))
check({r["clinic_id"] for r in conn.execute("SELECT clinic_id FROM staff_clinics WHERE staff_id=?", (doc_id,))} == {1, 2, 3}, "three clinics saved")
page = doc.get("/staff/profile").data.decode()
check(page.count(" checked") == 3, "all three show as ticked when reopening the profile")
r = doc.post("/staff/profile", data=dict(full_name="X", specialization="", contact_number="123", clinic_ids=["1"]), follow_redirects=True)
check(b"valid Philippine mobile" in r.data and conn.execute("SELECT COUNT(*) c FROM staff_clinics WHERE staff_id=?", (doc_id,)).fetchone()["c"] == 3,
      "an unreachable phone number is refused and nothing is changed")

pat = signup_patient("juan@test.com")
for cid in (1, 2, 3):
    pat.post("/appointment/new", data={"clinic_id": str(cid)})
    check(b"no assigned doctor" not in pat.get("/appointment/new/datetime").data, f"patient can book the doctor at clinic {cid}")
doc.post("/staff/profile", data=dict(full_name="Dr. Juan Santos", specialization="GP", contact_number="0917-000-0000", clinic_ids=["1"]))
pat.post("/appointment/new", data={"clinic_id": "2"})
check(b"no assigned doctor" in pat.get("/appointment/new/datetime").data, "after un-ticking clinic 2, patients can no longer book there")

# staff who aren't approved are never offered to patients
conn.execute("INSERT INTO clinics (name, address) VALUES ('Pending-only Clinic', 'x')"); conn.commit()
cid4 = conn.execute("SELECT id FROM clinics WHERE name='Pending-only Clinic'").fetchone()["id"]
conn.execute("INSERT INTO users (role, full_name, email, password_hash, status) VALUES ('staff','Pending Doc','p@t.com','x','pending')"); conn.commit()
pd = conn.execute("SELECT id FROM users WHERE email='p@t.com'").fetchone()["id"]
conn.execute("INSERT INTO staff_clinics (staff_id, clinic_id) VALUES (?, ?)", (pd, cid4)); conn.commit()
pat.post("/appointment/new", data={"clinic_id": str(cid4)})
check(b"no assigned doctor" in pat.get("/appointment/new/datetime").data, "a clinic whose only staff is pending approval can't be booked")

page = doc.get("/staff/clinics").data.decode()
check("1 staff" in page, "clinics page counts approved staff only")
r = doc.post("/staff/clinics/1/delete", follow_redirects=True)
check(conn.execute("SELECT COUNT(*) c FROM clinics WHERE id=1").fetchone()["c"] == 1, "a clinic that still has staff can't be deleted")

# ================= schedule: Sunday first, weekend colours, Morning / Afternoon =================
page = doc.get("/staff/schedule").data.decode()
left = page.split("schedule-left")[1].split("schedule-right")[0]
days = [d for d in re.findall(r"<h3[^>]*>(Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday)</h3>", left)]
check(days == ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"], f"weekly cards run Sunday -> Saturday ({days[:2]}...)")
check('<h3 class="wk-sun">Sunday</h3>' in left and '<h3 class="wk-sat">Saturday</h3>' in left and '<h3 class="">Monday</h3>' in left, "Saturday and Sunday headings get their own colour classes")
opts = re.findall(r'<option value="(\d)">(\w+)</option>', left)
check(opts[0] == ("6", "Sunday") and opts[-1] == ("5", "Saturday"), "'Add schedule' day list starts at Sunday but still stores 0=Mon..6=Sun")
doc.post("/staff/schedule", data=dict(weekday="6", start_time="09:00", end_time="11:00"))
check(conn.execute("SELECT COUNT(*) c FROM schedules WHERE staff_id=? AND weekday=6", (doc_id,)).fetchone()["c"] == 1, "a Sunday block is stored as weekday 6")
right = page.split("schedule-right")[1]
check('<span class="wk-sun">Su</span><span>Mo</span>' in right and '<span class="wk-sat">Sa</span>' in right, "calendar header is Su Mo Tu We Th Fr Sa with coloured Su / Sa")
check(re.search(r"calendar-day[^\"]*wk-sun", right) and re.search(r"calendar-day[^\"]*wk-sat", right), "weekend day cells carry the colour classes")
first_row = re.findall(r'calendar-week">(.*?)</div>', right, flags=re.S)[0]
check(len(re.findall(r"calendar-day", first_row)) == 7, "each calendar row has 7 cells")

# slots: Morning group before Afternoon group
page = doc.get("/staff/schedule?date=" + next(d for d in re.findall(r"date=(\d{4}-\d\d-\d\d)", doc.get("/staff/schedule").data.decode())
       if __import__("datetime").date.fromisoformat(d).weekday() in (0, 3) and __import__("datetime").date.fromisoformat(d) > __import__("datetime").date.today())).data.decode()
i_m, i_9, i_a, i_1 = (page.find(x) for x in ("Morning", "9:00 AM", "Afternoon", "1:00 PM"))
check(0 < i_m < i_9 < i_a < i_1, "slots are split under 'Morning' (AM) then 'Afternoon' (PM) headings")
block = page.split("Afternoon")[0]
check("PM</span>" not in block.split("Morning")[-1], "no PM slot appears in the Morning group")

# patient calendar also Sunday-first
pat.post("/appointment/new", data={"clinic_id": "1"})
page = pat.get("/appointment/new/datetime").data.decode()
check('<span class="wk-sun">Su</span><span>Mo</span>' in page, "patient's booking calendar starts on Sunday too")

# ================= nav order =================
nav = doc.get("/staff/dashboard").data.decode().split('id="navLinks"')[1].split("lang-switch")[0]
labels = re.findall(r">([^<>]+)</a>", nav)
check(labels == ["Dashboard", "Requests", "History", "My Schedule", "Clinics", "My Profile"], f"staff nav order: {labels}")

# ================= toasts + patient notifications =================
for i, (d, t) in enumerate([("2026-12-14", "09:00"), ("2026-12-15", "10:00")], 1):
    conn.execute("INSERT INTO appointments (patient_id,staff_id,clinic_id,appt_date,appt_time,reason,status) "
                 "VALUES ((SELECT id FROM users WHERE email='juan@test.com'),?,1,?,?,'checkup','Pending')", (doc_id, d, t))
conn.commit()
appt_ids = [r["id"] for r in conn.execute("SELECT id FROM appointments ORDER BY id")]
check(pat.get("/api/notifications").get_json()["items"] == [], "no notifications before anything happens")
r = doc.post(f"/staff/requests/{appt_ids[0]}", data={"decision": "approve"}, follow_redirects=True)
check(b'class="toast toast-success"' in r.data and b"flash-toast" not in r.data, "staff sees a green toast (bottom-left stack) after approving")
r = doc.post(f"/staff/requests/{appt_ids[1]}", data={"decision": "reschedule"}, follow_redirects=True)
check(b'class="toast toast-warn"' in r.data, "staff sees an orange toast after asking to reschedule")
items = pat.get("/api/notifications").get_json()["items"]
check([i["kind"] for i in items] == ["approved", "reschedule"], "patient has one notification per decision, oldest first")
check("Alabat Rural Health Unit" in items[0]["text"] and "2026-12-14" in items[0]["text"] and "approved" in items[0]["text"], f"notification text: {items[0]['text']}")
check("rescheduled" in items[1]["text"], "reschedule notification asks them to pick a new slot")
check(pat.get("/api/notifications").get_json()["items"] == [], "each notification is delivered only once")
pat.get("/lang/tl")
conn.execute("INSERT INTO notifications (user_id, kind, appointment_id) SELECT user_id, 'approved', id FROM (SELECT patient_id AS user_id, id FROM appointments LIMIT 1)"); conn.commit()
check("Naaprubahan" in pat.get("/api/notifications").get_json()["items"][0]["text"], "notifications follow the patient's language (Tagalog)")
pat.get("/lang/en")
check(doc.get("/api/notifications").status_code == 403, "staff can't read patient notifications")
check(app.test_client().get("/api/notifications").status_code == 302, "logged-out visitors are sent to login")
page = pat.get("/dashboard").data.decode()
check("pollNotifications" in page and "toastStack" in page, "patient pages poll for notifications and have the toast stack")
check("pollNotifications" not in doc.get("/staff/dashboard").data.decode(), "staff pages don't poll")

# ================= ending the AI conversation =================
aid = appt_ids[0]
pat.get("/appointments")                                  # seeds the thread (AI reply generated once)
n_msgs = lambda: conn.execute("SELECT COUNT(*) c FROM appointment_messages WHERE appointment_id=?", (aid,)).fetchone()["c"]
page = pat.get("/appointments").data.decode()
check("ai-continue-form" in page and "End conversation" in page, "open conversation shows the message box and an 'End conversation' button")
before = n_msgs(); pat.post(f"/appointments/{aid}/message", data={"message": "my head hurts"})
check(n_msgs() == before + 2, "while open, a message gets an AI reply")
pat.post(f"/appointments/{aid}/chat/end")
page = pat.get("/appointments").data.decode()
card = page.split(f'id="appt-{aid}"')[1].split('id="appt-')[0]
check("Conversation ended" in card and "ai-continue-form" not in card and "Continue conversation" in card, "ended: message box gone, note + 'Continue conversation' shown")
check("my head hurts" in card, "...the earlier messages stay readable")
before, calls = n_msgs(), len(ai_calls)
pat.post(f"/appointments/{aid}/message", data={"message": "still there?"})
check(n_msgs() == before and len(ai_calls) == calls, "an ended conversation ignores new messages and never calls the AI")
check(conn.execute("SELECT status FROM appointments WHERE id=?", (aid,)).fetchone()["status"] == "Approved", "the appointment itself is untouched")
other = signup_patient("other@test.com", "Otro")
check(other.post(f"/appointments/{aid}/chat/resume").status_code == 404, "another patient can't resume (or end) someone else's chat")
pat.post(f"/appointments/{aid}/chat/resume")
check("ai-continue-form" in pat.get("/appointments").data.decode().split(f'id="appt-{aid}"')[1].split('id="appt-')[0], "resumed: the message box is back")
before = n_msgs(); pat.post(f"/appointments/{aid}/message", data={"message": "back again"})
check(n_msgs() == before + 2, "...and the AI replies again")
# ending before any AI note exists: no AI note is generated afterwards
conn.execute("INSERT INTO appointments (patient_id,staff_id,clinic_id,appt_date,appt_time,reason,status) "
             "VALUES ((SELECT id FROM users WHERE email='juan@test.com'),?,1,'2026-12-20','11:00','cough','Pending')", (doc_id,)); conn.commit()
new_id = conn.execute("SELECT MAX(id) m FROM appointments").fetchone()["m"]
pat.post(f"/appointments/{new_id}/chat/end"); calls = len(ai_calls); pat.get("/appointments")
check(len(ai_calls) == calls, "ending a chat before its first AI note stops that note from being generated")

print(f"\n{'ALL PASSED' if not failed else str(failed) + ' FAILED'}")
sys.exit(1 if failed else 0)
