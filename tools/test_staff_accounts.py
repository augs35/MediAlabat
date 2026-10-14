"""
Checks the doctor/staff sign-in security: one-time passcodes at sign-up and log-in, approval of new staff accounts,
and the ways someone might try to get around them. Uses a throwaway database; never touches your real one.

    python tools/test_staff_accounts.py
"""
import os, re, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(tempfile.mkdtemp())
os.environ["SECRET_KEY"] = "test-secret"
os.environ.pop("STAFF_OTP_REQUIRED", None)

import app as app_module                      # noqa: E402
import sms                                    # noqa: E402
from app import app                           # noqa: E402
from db import init_db, get_db                # noqa: E402

init_db()
conn = get_db()
sent = []
fake = lambda to, msg: (sent.append((to, msg)) or True)
sms.send_sms = fake                           # OTP texts
app_module.send_sms = fake                    # approval / rejection texts
failed = 0


def check(cond, label):
    global failed
    failed += 0 if cond else 1
    print(("PASS  " if cond else "FAIL  ") + label)


def code_sent():
    return re.search(r"code is (\d{6})", [m for _, m in sent if "verification code" in m][-1]).group(1)


def age_codes():
    conn.execute("UPDATE otp_codes SET created_at = datetime('now','-2 minutes')"); conn.commit()


def where(r):
    return r.headers.get("Location", "")


STAFF_FORM = dict(first_name="Maria", middle_name="", last_name="Reyes", email="maria@test.com", password="secret123",
                  contact_number="0917-555-0101", specialization="Pediatrics", clinic_ids=["1", "2"])
doctor = dict(email="doctor@medialabat.test", password="password123")

# ---------- patients are NOT affected ----------
c = app.test_client()
c.post("/signup", data=dict(first_name="Juan", middle_name="", last_name="Cruz", email="juan@test.com", password="secret123",
       contact_number="0917-111-2222", date_of_birth="2003-05-10", sex="Male", address="x",
       emergency_contact_name="M", emergency_contact_relation="Mother", emergency_contact_number="0918"))
n = len(sent)
r = c.post("/login", data=dict(email="juan@test.com", password="secret123"))
check(where(r).endswith("/dashboard") and len(sent) == n, "patient logs in with no passcode and no text sent")

# ---------- staff login needs the passcode ----------
s = app.test_client()
s.get("/lang/tl")
r = s.post("/login", data=doctor)
check(where(r).endswith("/otp"), "staff password alone -> sent to the passcode page")
check(s.get("/staff/dashboard").status_code == 302, "...and is NOT logged in yet (dashboard redirects)")
check(sent and sent[-1][0] == "0917-000-0000", "code texted to the staff member's own number")
code = code_sent()
page = s.get("/otp").data.decode()
check("0917 *** **00" in page and code not in page, "page shows the masked number, never the code")
wrong = "000000" if code != "000000" else "111111"
check(b"4" in s.post("/otp", data={"code": wrong}).data and s.get("/staff/dashboard").status_code == 302, "wrong code: refused, still not logged in")
r = s.post("/otp", data={"code": code})
check(where(r).endswith("/staff/dashboard") and s.get("/staff/dashboard").status_code == 200, "right code -> logged in")
check(b"Maligayang" in s.get("/staff/dashboard").data, "language choice (Tagalog) survives the login")

# ---------- wrong password / unknown user never reach the passcode ----------
n = len(sent)
r = app.test_client().post("/login", data=dict(email="doctor@medialabat.test", password="nope"))
check(r.status_code == 200 and len(sent) == n, "wrong password -> no code is sent")

# ---------- no passcode session -> nothing to submit ----------
check(where(app.test_client().get("/otp")).endswith("/login"), "/otp without a login attempt redirects to login")
t = app.test_client(); t.post("/login", data=doctor)
with t.session_transaction() as ss:
    ss["otp_pending"] = {**ss["otp_pending"], "at": ss["otp_pending"]["at"] - 3600}   # re-assign: nested edits aren't saved
check(where(t.post("/otp", data={"code": "123456"})).endswith("/login"), "a stale passcode session (>15 min) is rejected")

# ---------- lockout + resend rules ----------
age_codes(); t = app.test_client(); t.post("/login", data=doctor); code = code_sent()
bad = "999999" if code != "999999" else "888888"
for _ in range(5):
    t.post("/otp", data={"code": bad})
check(where(t.post("/otp", data={"code": code})) == "" and t.get("/staff/dashboard").status_code == 302, "after 5 wrong tries even the right code is refused")
r = t.post("/otp/resend", follow_redirects=True)
check(b"wait" in r.data.lower(), "asking for a new code immediately -> told to wait")
age_codes(); t.post("/otp/resend"); fresh = code_sent()
check(where(t.post("/otp", data={"code": fresh})).endswith("/staff/dashboard"), "after the cooldown a new code works")

# ---------- staff sign-up: valid phone, phone check, then approval ----------
a = app.test_client()
r = a.post("/staff/signup", data={**STAFF_FORM, "contact_number": "12345"})
check(r.status_code == 200 and conn.execute("SELECT COUNT(*) c FROM users WHERE email='maria@test.com'").fetchone()["c"] == 0,
      "sign-up with a number that can't receive codes is refused")
r = a.post("/staff/signup", data=STAFF_FORM)
check(where(r).endswith("/otp"), "valid sign-up -> phone verification page")
row = conn.execute("SELECT id, status FROM users WHERE email='maria@test.com'").fetchone()
mid = row["id"]
check(row["status"] == "unverified", "account starts 'unverified'")
check({r["clinic_id"] for r in conn.execute("SELECT clinic_id FROM staff_clinics WHERE staff_id=?", (mid,))} == {1, 2}, "both chosen clinics saved")
check(a.get("/staff/dashboard").status_code == 302, "unverified account can't use the staff pages")
r = a.post("/otp", data={"code": code_sent()})
check(where(r).endswith("/login") and conn.execute("SELECT status FROM users WHERE id=?", (mid,)).fetchone()["status"] == "pending",
      "phone verified -> account 'pending' (not active)")
age_codes()
r = app.test_client().post("/login", data=dict(email="maria@test.com", password="secret123"))
check(r.status_code == 200 and b"waiting for approval" in r.data, "pending account sees 'waiting for approval' and gets no code")
n = len(sent)
check(len(sent) == n, "no passcode is sent to a pending account")

# forged session for a pending account
f = app.test_client()
with f.session_transaction() as ss:
    ss["user_id"] = mid
check(f.get("/staff/dashboard").status_code == 302 and f.get("/staff/accounts").status_code == 302, "a forged session for a pending account is thrown out")

# ---------- an existing staff member approves ----------
s = app.test_client(); s.post("/login", data=doctor); s.post("/otp", data={"code": code_sent()})
page = s.get("/staff/accounts").data.decode()
check("Maria Reyes" in page and "0917-555-0101" in page, "approver sees the request with its details")
check(b"1 waiting" in s.get("/staff/dashboard").data, "dashboard shows 1 waiting")
check(s.post(f"/staff/accounts/{conn.execute('SELECT id FROM users WHERE role=\"staff\" ORDER BY id LIMIT 1').fetchone()['id']}/decision",
             data={"decision": "approve"}).status_code == 403, "nobody can approve their own account")
r = s.post(f"/staff/accounts/{mid}/decision", data={"decision": "approve"}, follow_redirects=True)
check(conn.execute("SELECT status, reviewed_by FROM users WHERE id=?", (mid,)).fetchone()["status"] == "active", "approved -> active")
check(sent[-1][0] == "0917-555-0101" and "approved" in sent[-1][1], "applicant is texted that it was approved")
check(b"toast-success" in r.data, "approver gets a toast")
n = len(sent); s.post(f"/staff/accounts/{mid}/decision", data={"decision": "reject"})
check(conn.execute("SELECT status FROM users WHERE id=?", (mid,)).fetchone()["status"] == "active" and len(sent) == n, "a second decision on the same account is ignored")
check("Maria Reyes" in s.get("/staff/accounts").data.decode() and b"Approved" in s.get("/staff/accounts").data, "decision appears under 'recently decided'")

# approved staff still need a passcode on every login
m = app.test_client()
check(where(m.post("/login", data=dict(email="maria@test.com", password="secret123"))).endswith("/otp"), "approved staff: password -> still asked for a passcode")
check(m.get("/staff/dashboard").status_code == 302, "...so not logged in yet")
check(where(m.post("/otp", data={"code": code_sent()})).endswith("/staff/dashboard"), "...and the code logs them in")

# ---------- rejection ----------
b = app.test_client(); b.post("/staff/signup", data={**STAFF_FORM, "email": "pedro@test.com", "first_name": "Pedro", "contact_number": "0917-555-0202"})
b.post("/otp", data={"code": code_sent()})
pid = conn.execute("SELECT id FROM users WHERE email='pedro@test.com'").fetchone()["id"]
s.post(f"/staff/accounts/{pid}/decision", data={"decision": "reject"})
check(conn.execute("SELECT status FROM users WHERE id=?", (pid,)).fetchone()["status"] == "rejected" and "not approved" in sent[-1][1], "rejected -> status 'rejected' and applicant is texted")
n = len(sent)
r = app.test_client().post("/login", data=dict(email="pedro@test.com", password="secret123"))
check(b"wasn&#39;t approved" in r.data and len(sent) == n, "rejected account can't log in and gets no code")

# ---------- patients can't see staff-only things ----------
check(c.get("/staff/accounts").status_code == 403, "a patient can't open the account-review page")
check(c.post(f"/staff/accounts/{pid}/decision", data={"decision": "approve"}).status_code == 403, "...or approve anyone")

# ---------- the kill switch ----------
os.environ["STAFF_OTP_REQUIRED"] = "0"
k = app.test_client()
check(where(k.post("/login", data=doctor)).endswith("/staff/dashboard"), "STAFF_OTP_REQUIRED=0: staff log in directly")
k.post("/staff/signup", data={**STAFF_FORM, "email": "ana@test.com", "first_name": "Ana", "contact_number": "0917-555-0303"})
check(conn.execute("SELECT status FROM users WHERE email='ana@test.com'").fetchone()["status"] == "pending", "...and sign-ups skip the phone check but still wait for approval")
os.environ.pop("STAFF_OTP_REQUIRED")

# ---------- sessions are signed with a real secret ----------
from itsdangerous import URLSafeTimedSerializer                     # noqa: E402
from flask.sessions import SecureCookieSessionInterface              # noqa: E402
forged = SecureCookieSessionInterface().get_signing_serializer(type("A", (), {"secret_key": "dev-secret-change-me", "config": app.config})())
fc = app.test_client()
fc.set_cookie("session", forged.dumps({"user_id": 1}))
check(fc.get("/staff/dashboard").status_code == 302, "a cookie signed with the old hard-coded key no longer works")

print(f"\n{'ALL PASSED' if not failed else str(failed) + ' FAILED'}")
sys.exit(1 if failed else 0)
