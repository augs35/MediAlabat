"""
Checks the server side of the SMS gateway: auth, queueing from approve/reschedule, claiming, retries, leases.
Runs against a throwaway database in a temp folder — it never touches your real medialabat.db.

    python tools/test_sms_gateway.py
"""
import os, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(tempfile.mkdtemp())                       # db.py uses a relative path -> a brand-new DB lives here
os.environ["SECRET_KEY"] = "test-secret"           # so the test never creates a .secret_key file in your project
os.environ["SMS_PROVIDER"] = "esp32"
os.environ["SMS_DEVICE_KEY"] = "testkey"

from app import app                                # noqa: E402
from db import init_db, get_db                      # noqa: E402

init_db()
client, H, conn = app.test_client(), {"X-Device-Key": "testkey"}, get_db()
failed = 0


def check(cond, label):
    global failed
    failed += 0 if cond else 1
    print(("PASS  " if cond else "FAIL  ") + label)


check(client.get("/api/sms/next").status_code == 403, "no key -> 403")
check(client.get("/api/sms/next", headers={"X-Device-Key": "nope"}).status_code == 403, "wrong key -> 403")
check(client.get("/api/sms/next", headers=H).status_code == 204, "empty queue -> 204")

signup = dict(first_name="Juan", middle_name="", last_name="Cruz", email="juan@test.com", password="secret123",
              contact_number="0917-111-2222", date_of_birth="2003-05-10", sex="Male", address="x",
              emergency_contact_name="M", emergency_contact_relation="Mother", emergency_contact_number="0918")
client.post("/signup", data=signup)
client.post("/signup", data={**signup, "email": "bad@test.com", "contact_number": "12345"})
sid = conn.execute("SELECT id FROM users WHERE role='staff'").fetchone()["id"]
for email, d, t in [("juan@test.com", "2026-10-12", "09:00"), ("juan@test.com", "2026-10-13", "10:30"), ("bad@test.com", "2026-10-15", "13:00")]:
    pid = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"]
    conn.execute("INSERT INTO appointments (patient_id,staff_id,clinic_id,appt_date,appt_time,reason,status) "
                 "VALUES (?,?,1,?,?,'x','Pending')", (pid, sid, d, t))
conn.commit()
with client.session_transaction() as s:
    s["user_id"] = sid
check(b"SMS queued" in client.post("/staff/requests/1", data={"decision": "approve"}, follow_redirects=True).data,
      "approving queues an SMS (and says so)")
client.post("/staff/requests/2", data={"decision": "reschedule"})
client.post("/staff/requests/3", data={"decision": "approve"})
rows = [dict(r) for r in conn.execute("SELECT * FROM sms_outbox ORDER BY id")]
check(rows[0]["to_number"] == "+639171112222" and rows[0]["status"] == "pending", "number normalised to +639171112222")
check("APPROVED" in rows[0]["message"] and "RESCHEDULED" in rows[1]["message"], "approved / rescheduled wording")
check(rows[2]["status"] == "failed" and rows[2]["error"] == "invalid phone number", "unusable number fails immediately")

r = client.get("/api/sms/next?rssi=17", headers=H)
parts = r.data.decode().split("\t")
check(r.status_code == 200 and len(parts) == 3 and parts[0] == "1", "claim returns 'id<TAB>number<TAB>text'")
check(client.get("/api/sms/next", headers=H).data.decode().startswith("2\t"), "next poll gets the next message, not a duplicate")
check(client.get("/api/sms/next", headers=H).status_code == 204, "nothing deliverable left -> 204")
check(conn.execute("SELECT rssi FROM sms_gateway").fetchone()["rssi"] == 17, "signal strength recorded")

check(client.post("/api/sms/1/result", headers=H, data={"ok": "1"}).status_code == 200, "success reported")
check(conn.execute("SELECT status FROM sms_outbox WHERE id=1").fetchone()["status"] == "sent", "-> sent")
check(client.post("/api/sms/1/result", headers=H, data={"ok": "1"}).status_code == 404, "double report rejected")

client.post("/api/sms/2/result", headers=H, data={"ok": "0", "error": "+CMS ERROR: 330"})
check(conn.execute("SELECT status FROM sms_outbox WHERE id=2").fetchone()["status"] == "pending", "1st failure -> retried later")
for _ in (2, 3):
    client.get("/api/sms/next", headers=H)
    client.post("/api/sms/2/result", headers=H, data={"ok": "0", "error": "again"})
check(conn.execute("SELECT status, attempts FROM sms_outbox WHERE id=2").fetchone()["status"] == "failed", "gives up after 3 attempts")

conn.execute("INSERT INTO sms_outbox (to_number, message) VALUES ('+639170000000', 'lease')"); conn.commit()
lid = conn.execute("SELECT MAX(id) m FROM sms_outbox").fetchone()["m"]
client.get("/api/sms/next", headers=H)
check(client.get("/api/sms/next", headers=H).status_code == 204, "fresh claim isn't re-offered")
conn.execute("UPDATE sms_outbox SET claimed_at=datetime('now','-10 minutes') WHERE id=?", (lid,)); conn.commit()
check(client.get("/api/sms/next", headers=H).data.decode().startswith(f"{lid}\t"), "expired claim (gateway died) is re-offered")
conn.execute("UPDATE sms_outbox SET attempts=3, claimed_at=datetime('now','-10 minutes') WHERE id=?", (lid,)); conn.commit()
check(client.get("/api/sms/next", headers=H).status_code == 204, "expired claim with no attempts left is not re-offered")

check(client.get("/staff/sms").status_code == 200, "staff status page renders")
with client.session_transaction() as s:
    s["user_id"] = conn.execute("SELECT id FROM users WHERE email='juan@test.com'").fetchone()["id"]
check(client.get("/staff/sms").status_code == 403, "patients can't open it")

print(f"\n{'ALL PASSED' if not failed else str(failed) + ' FAILED'}")
sys.exit(1 if failed else 0)
