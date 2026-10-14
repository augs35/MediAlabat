"""Unit checks of the one-time-passcode rules (expiry, lockout, cooldown, hourly cap). Uses a throwaway database.

    python tools/test_otp.py
"""
import os, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(tempfile.mkdtemp())
import otp, sms                                   # noqa: E402
from db import init_db, get_db                    # noqa: E402

init_db()
conn = get_db()
sent = []
sms.send_sms = lambda to, msg: sent.append((to, msg)) or True
uid = conn.execute("SELECT id FROM users LIMIT 1").fetchone()["id"]
failed = 0


def check(cond, label):
    global failed
    failed += 0 if cond else 1
    print(("PASS  " if cond else "FAIL  ") + label)


age = lambda: (conn.execute("UPDATE otp_codes SET created_at = datetime('now','-1 minute')"), conn.commit())
last = lambda: sent[-1][1].split("is ")[1][:6]

st, _ = otp.issue(conn, uid, "login", "0917-111-2222"); code = last()
check(st == "sent" and len(code) == 6 and code.isdigit(), f"6-digit code issued and texted: {sent[-1][1]}")
check(conn.execute("SELECT code_hash FROM otp_codes").fetchone()[0] != code, "only a salted hash is stored")
check(otp.issue(conn, uid, "login", "0917")[0] == "cooldown", "a 2nd code while the first is unused -> cooldown")
check(otp.verify(conn, uid, "signup", code)[0] == "none", "a login code can't be used for signup")
wrong = "000000" if code != "000000" else "111111"
check(otp.verify(conn, uid, "login", wrong) == ("wrong", 4), "wrong code -> 4 tries left")
check(otp.verify(conn, uid, "login", code[:3] + " " + code[3:])[0] == "ok", "right code (typed with a space) accepted")
check(otp.verify(conn, uid, "login", code)[0] == "none", "a code works only once")
check(otp.issue(conn, uid, "login", "0917")[0] == "sent", "right after a SUCCESSFUL login, the next login gets a code immediately")

age(); otp.issue(conn, uid, "login", "0917"); c2 = last(); bad = "999999" if c2 != "999999" else "888888"
res = [otp.verify(conn, uid, "login", bad)[0] for _ in range(5)]
check(res == ["wrong"] * 4 + ["locked"], f"5th wrong guess locks the code {res}")
check(otp.verify(conn, uid, "login", c2)[0] == "locked", "...and the right code no longer works")

age(); otp.issue(conn, uid, "login", "0917"); c3 = last()
conn.execute("UPDATE otp_codes SET expires_at = datetime('now','-1 second') WHERE used = 0"); conn.commit()
check(otp.verify(conn, uid, "login", c3)[0] == "expired", "expired code rejected")
check(otp.issue(conn, uid, "login", "0917")[0] == "sent", "...and an expired code doesn't block asking for a fresh one")

age(); otp.issue(conn, uid, "login", "0917"); first = last()
age(); otp.issue(conn, uid, "login", "0917"); second = last()
check(first != second and otp.verify(conn, uid, "login", first)[0] == "wrong", "an older code stops working once a newer one is issued")
check(otp.verify(conn, uid, "login", second)[0] == "ok", "...while the newest works")

conn.execute("DELETE FROM otp_codes"); conn.commit()
for _ in range(10):
    age(); otp.issue(conn, uid, "login", "0917")
age()
check(otp.issue(conn, uid, "login", "0917")[0] == "too_many", "an 11th code within an hour -> too_many")
check(otp.mask_phone("0917-111-2222") == "0917 *** **22", "phone masked as 0917 *** **22")
print(f"\n{'ALL PASSED' if not failed else str(failed) + ' FAILED'}")
sys.exit(1 if failed else 0)
