// sim800_sms.h — talk to a SIM800L over a serial port: start it up, check the network, send one SMS.
// Pure Arduino API (Stream / String / millis / delay), no hardware pins in here, so the same code is
// compiled and exercised on a PC against a simulated modem (see tools/esp32_host_test/).
#pragma once
#include <Arduino.h>

class Sim800 {
 public:
  explicit Sim800(Stream& port) : _s(port) {}

  // Read until `token` shows up (true), an ERROR / +CME ERROR / +CMS ERROR shows up (false) or the timeout hits (false).
  // Everything received is returned in `captured` so callers can log it.
  bool waitFor(const char* token, uint32_t timeoutMs, String* captured = nullptr) {
    String buf;
    uint32_t start = millis();
    while (millis() - start < timeoutMs) {
      while (_s.available()) buf += (char)_s.read();
      if (buf.indexOf(token) >= 0) {
        if (captured) *captured = buf;
        return true;
      }
      if (buf.indexOf("ERROR") >= 0) {
        if (captured) *captured = buf;
        return false;
      }
      delay(5);  // on the ESP32 this also lets the Wi-Fi stack and watchdog run
    }
    if (captured) *captured = buf;
    return false;
  }

  // Send one AT command and wait for `expect`. Anything left over from earlier is thrown away first.
  bool command(const char* cmd, const char* expect = "OK", uint32_t timeoutMs = 2000, String* captured = nullptr) {
    drain();
    _s.print(cmd);
    _s.print("\r");
    bool ok = waitFor(expect, timeoutMs, captured);
    if (ok && strcmp(expect, "OK") != 0) waitFor("OK", 500);  // swallow the closing OK so it can't confuse the next command
    return ok;
  }

  // Bring the modem to a known state: answers AT, echo off, SIM unlocked, text-mode SMS.
  bool init(String& err) {
    bool alive = false;
    for (int i = 0; i < 10 && !alive; i++) alive = command("AT", "OK", 500);  // also lets it auto-detect the baud rate
    if (!alive) {
      err = "no answer to AT (power? wiring? baud rate?)";
      return false;
    }
    command("ATE0");       // echo off
    command("AT+CMEE=2");  // readable error texts instead of bare numbers
    String r;
    if (!command("AT+CPIN?", "READY", 3000, &r)) {
      err = "SIM not ready: " + brief(r);
      return false;
    }
    if (!command("AT+CMGF=1")) {  // text mode
      err = "could not switch to SMS text mode";
      return false;
    }
    command("AT+CSCS=\"IRA\"");        // plain ASCII
    command("AT+CNMI=0,0,0,0,0");      // never push incoming SMS at us — we only send
    return true;
  }

  // +CREG: <n>,<stat>   stat: 0 not searching, 1 registered (home), 2 searching, 3 denied, 5 registered (roaming)
  int registration() {
    String r;
    if (!command("AT+CREG?", "+CREG:", 2000, &r)) return -1;
    int p = r.indexOf("+CREG:");
    int comma = r.indexOf(',', p);
    if (comma < 0) return -1;
    return r.substring(comma + 1).toInt();
  }

  bool isRegistered() {
    int s = registration();
    return s == 1 || s == 5;
  }

  bool waitRegistered(uint32_t timeoutMs) {
    uint32_t start = millis();
    while (millis() - start < timeoutMs) {
      if (isRegistered()) return true;
      delay(2000);
    }
    return false;
  }

  // Signal quality 0..31 (higher is better, ~10+ is workable), 99 = unknown, -1 = the modem didn't answer.
  int signal() {
    String r;
    if (!command("AT+CSQ", "+CSQ:", 2000, &r)) return -1;
    int p = r.indexOf("+CSQ:");
    return r.substring(p + 5).toInt();
  }

  // Send one text. Returns true only when the network accepted it (+CMGS: <reference>).
  bool sendSms(const String& number, const String& rawText, String& err) {
    String text = sanitize(rawText);
    drain();
    _s.print("AT+CMGS=\"");
    _s.print(number);
    _s.print("\"\r");

    String resp;
    if (!waitFor(">", 8000, &resp)) {
      err = "no '>' prompt from modem: " + brief(resp);
      _s.write((uint8_t)27);  // ESC: cancel, so the modem doesn't stay stuck waiting for text
      delay(300);
      drain();
      return false;
    }
    _s.print(text);
    _s.write((uint8_t)26);  // Ctrl+Z = "send it"

    if (waitFor("+CMGS:", 90000, &resp)) {
      waitFor("OK", 2000);
      return true;
    }
    err = "network did not accept the SMS: " + brief(resp);
    return false;
  }

 private:
  Stream& _s;

  void drain() {
    while (_s.available()) _s.read();
  }

  // Control characters inside the body would end or cancel the message early.
  static String sanitize(const String& in) {
    String out;
    for (unsigned i = 0; i < in.length(); i++) {
      char c = in[i];
      out += (c < 32 || c == 127) ? ' ' : c;
    }
    return out;
  }

  static String brief(String s) {
    s.replace("\r", " ");
    s.replace("\n", " ");
    s.trim();
    if (s.length() > 80) s = s.substring(0, 80);
    return s.length() ? s : String("(nothing received)");
  }
};
