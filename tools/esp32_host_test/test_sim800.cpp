// Runs the real sim800_sms.h against a simulated SIM800L and checks how it behaves, including the failure cases.
#include <Arduino.h>
#include <deque>
#include <vector>
#include <cstdio>
#include "sim800_sms.h"

struct FakeSim800 : Stream {
  // ---- behaviour knobs ----
  bool alive = true, echo = true, noisy = false, simReady = true;
  std::vector<int> regSeq{1};
  enum SmsMode { SMS_OK, SMS_CMS_ERROR, SMS_NO_PROMPT, SMS_SILENT } smsMode = SMS_OK;
  uint32_t smsLatencyMs = 3000, cmdLatencyMs = 20;
  // ---- what it saw ----
  std::vector<std::string> commands;
  std::string smsTo, smsBody;
  int ctrlZ = 0;
  bool gotEsc = false;

  std::deque<std::pair<uint32_t, char>> out;  // (time it becomes readable, char)
  std::string line, body;
  bool inBody = false;
  size_t regIdx = 0;

  void say(const std::string& s, uint32_t latency) { for (char c : s) out.push_back({millis() + latency, c}); }
  void respond(const std::string& s) {
    if (noisy) say("\r\n+CREG: 1\r\n\r\nSMS Ready\r\n", cmdLatencyMs);   // unsolicited chatter before the real answer
    say(s, cmdLatencyMs);
    if (noisy) say("\r\n+CSQ: 20,0\r\n", cmdLatencyMs);
  }
  int available() override { return (!out.empty() && out.front().first <= millis()) ? 1 : 0; }
  int read() override { int c = (unsigned char)out.front().second; out.pop_front(); return c; }

  size_t write(uint8_t b) override {
    if (!alive) return 1;
    if (inBody) {
      if (b == 0x1A) {                       // Ctrl+Z: message complete
        inBody = false; smsBody = body; ctrlZ++;
        if (smsMode == SMS_OK) say("\r\n+CMGS: 42\r\n\r\nOK\r\n", smsLatencyMs);
        else if (smsMode == SMS_CMS_ERROR) say("\r\n+CMS ERROR: 330\r\n", smsLatencyMs);
      } else if (b == 0x1B) { inBody = false; gotEsc = true; }
      else body += (char)b;
      return 1;
    }
    if (b == 0x1B) { gotEsc = true; return 1; }
    if (echo) say(std::string(1, (char)b), 0);
    if (b == '\r') { handle(line); line.clear(); } else line += (char)b;
    return 1;
  }

  void handle(const std::string& c) {
    commands.push_back(c);
    if (c == "AT" || c == "AT+CMEE=2" || c == "AT+CMGF=1" || c == "AT+CSCS=\"IRA\"" || c == "AT+CNMI=0,0,0,0,0") respond("\r\nOK\r\n");
    else if (c == "ATE0") { echo = false; respond("\r\nOK\r\n"); }
    else if (c == "AT+CPIN?") respond(simReady ? "\r\n+CPIN: READY\r\n\r\nOK\r\n" : "\r\n+CPIN: SIM PIN\r\n\r\nOK\r\n");
    else if (c == "AT+CREG?") { int s = regSeq[std::min(regIdx++, regSeq.size() - 1)]; respond("\r\n+CREG: 0," + std::to_string(s) + "\r\n\r\nOK\r\n"); }
    else if (c == "AT+CSQ") respond("\r\n+CSQ: 17,0\r\n\r\nOK\r\n");
    else if (c.rfind("AT+CMGS=\"", 0) == 0) {
      smsTo = c.substr(9, c.size() - 10);
      if (smsMode != SMS_NO_PROMPT) { say("\r\n> ", cmdLatencyMs); inBody = true; body.clear(); }
    } else respond("\r\nERROR\r\n");
  }
};

static int failures = 0, checks = 0;
#define CHECK(cond, label) do { checks++; if (cond) printf("PASS  %s\n", label); else { failures++; printf("FAIL  %s\n", label); } } while (0)
static bool has(const String& s, const char* t) { return s.indexOf(t) >= 0; }

int main() {
  { // 1. normal start-up, echo on, noisy line
    FakeSim800 f; f.noisy = true; Sim800 m(f); String err;
    CHECK(m.init(err), "init succeeds even with echo on and unsolicited chatter");
    CHECK(!f.echo, "echo switched off");
    bool order = f.commands.size() >= 5 && f.commands[0] == "AT" && f.commands[1] == "ATE0" && f.commands[3] == "AT+CPIN?" && f.commands[4] == "AT+CMGF=1";
    CHECK(order, "start-up commands sent in the right order");
  }
  { FakeSim800 f; f.alive = false; Sim800 m(f); String err; uint32_t t0 = millis();
    CHECK(!m.init(err) && has(err, "no answer"), "dead modem -> init fails with a useful message");
    CHECK(millis() - t0 < 10000, "...and gives up in reasonable time"); }
  { FakeSim800 f; f.simReady = false; Sim800 m(f); String err;
    CHECK(!m.init(err) && has(err, "SIM not ready") && has(err, "SIM PIN"), "PIN-locked SIM -> clear error naming the cause"); }

  { // 2. network registration
    FakeSim800 f; f.regSeq = {2, 2, 1}; Sim800 m(f);
    CHECK(m.waitRegistered(30000), "searching, searching, then registered -> waitRegistered true"); }
  { FakeSim800 f; f.regSeq = {2}; Sim800 m(f); uint32_t t0 = millis();
    CHECK(!m.waitRegistered(10000) && millis() - t0 >= 10000, "never registers -> false after the timeout"); }
  { FakeSim800 f; f.regSeq = {5}; Sim800 m(f); CHECK(m.isRegistered(), "roaming (5) counts as registered"); }
  { FakeSim800 f; f.regSeq = {3}; Sim800 m(f); CHECK(!m.isRegistered(), "registration denied (3) is not registered"); }
  { FakeSim800 f; f.noisy = true; Sim800 m(f); CHECK(m.signal() == 17, "signal quality parsed (17/31) despite noise"); }

  { // 3. sending
    FakeSim800 f; Sim800 m(f); String err; uint32_t t0 = millis();
    bool ok = m.sendSms("+639171112222", "MediAlabat: Your appointment was APPROVED.", err);
    CHECK(ok, "send succeeds");
    CHECK(f.smsTo == "+639171112222", "number passed to AT+CMGS correctly");
    CHECK(f.smsBody == "MediAlabat: Your appointment was APPROVED.", "text arrives unchanged");
    CHECK(f.ctrlZ == 1 && !f.gotEsc, "exactly one Ctrl+Z, no cancel");
    CHECK(millis() - t0 >= 3000, "waited for the network's answer (not just the prompt)"); }
  { FakeSim800 f; f.smsLatencyMs = 45000; Sim800 m(f); String err;
    CHECK(m.sendSms("+639171112222", "slow network", err), "a slow (45 s) network still counts as success"); }
  { FakeSim800 f; f.noisy = true; Sim800 m(f); String err;
    CHECK(m.sendSms("+639171112222", "noisy line", err), "send succeeds with unsolicited messages mixed in"); }
  { FakeSim800 f; Sim800 m(f); String err;
    m.sendSms("+639171112222", String("a\x1a" "b\x1b" "c\r\nd"), err);
    CHECK(f.smsBody == "a b c  d" && f.ctrlZ == 1 && !f.gotEsc, "control characters in the text can't end/cancel the SMS early"); }
  { FakeSim800 f; f.smsMode = FakeSim800::SMS_CMS_ERROR; Sim800 m(f); String err;
    CHECK(!m.sendSms("+639171112222", "x", err) && has(err, "330"), "+CMS ERROR from the network -> false, error text kept"); }
  { FakeSim800 f; f.smsMode = FakeSim800::SMS_NO_PROMPT; Sim800 m(f); String err;
    CHECK(!m.sendSms("+639171112222", "x", err) && has(err, "'>'") && f.gotEsc, "no '>' prompt -> false and the modem is sent ESC to cancel"); }
  { FakeSim800 f; f.smsMode = FakeSim800::SMS_SILENT; Sim800 m(f); String err; uint32_t t0 = millis();
    CHECK(!m.sendSms("+639171112222", "x", err) && has(err, "did not accept") && millis() - t0 >= 90000, "silence after sending -> false after 90 s, not a hang"); }

  printf("\n%d checks, %d failed\n", checks, failures);
  return failures ? 1 : 0;
}
