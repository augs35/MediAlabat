// Tiny stand-in for the Arduino core, just enough to compile and test sim800_sms.h on a PC.
// (Not used when building for the ESP32 — there the real Arduino.h is used.)
#pragma once
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <string>

inline uint32_t g_now_ms = 0;                         // simulated clock: advances only when the code calls delay()
inline uint32_t millis() { return g_now_ms; }
inline void delay(uint32_t ms) { g_now_ms += ms; }

class String {
 public:
  std::string s;
  String() {}
  String(const char* c) : s(c ? c : "") {}
  String(const std::string& c) : s(c) {}
  String(int v) : s(std::to_string(v)) {}
  String(long v) : s(std::to_string(v)) {}
  unsigned length() const { return (unsigned)s.size(); }
  const char* c_str() const { return s.c_str(); }
  char operator[](unsigned i) const { return s[i]; }
  String& operator+=(char c) { s += c; return *this; }
  String& operator+=(const char* c) { s += c; return *this; }
  String& operator+=(const String& o) { s += o.s; return *this; }
  int indexOf(const char* t) const { auto p = s.find(t); return p == std::string::npos ? -1 : (int)p; }
  int indexOf(char c, unsigned from = 0) const { auto p = s.find(c, from); return p == std::string::npos ? -1 : (int)p; }
  String substring(unsigned a, unsigned b) const { return String(s.substr(a, b > a ? b - a : 0)); }
  String substring(unsigned a) const { return String(a < s.size() ? s.substr(a) : std::string()); }
  long toInt() const { return std::atol(s.c_str()); }
  void trim() {
    size_t a = s.find_first_not_of(" \t\r\n"), b = s.find_last_not_of(" \t\r\n");
    s = (a == std::string::npos) ? std::string() : s.substr(a, b - a + 1);
  }
  void replace(const char* from, const char* to) {
    std::string f(from), t(to);
    for (size_t p = 0; (p = s.find(f, p)) != std::string::npos; p += t.size()) s.replace(p, f.size(), t);
  }
};
inline String operator+(const String& a, const String& b) { return String(a.s + b.s); }
inline String operator+(const char* a, const String& b) { return String(std::string(a) + b.s); }
inline String operator+(const String& a, const char* b) { return String(a.s + b); }

class Stream {
 public:
  virtual ~Stream() {}
  virtual int available() = 0;
  virtual int read() = 0;
  virtual size_t write(uint8_t) = 0;
  size_t print(const char* c) { size_t n = 0; while (*c) { write((uint8_t)*c++); n++; } return n; }
  size_t print(const String& v) { return print(v.c_str()); }
};

// ---- extras used by esp32_sms_gateway.ino (declarations only; this is a compile check, not a simulation) ----
#include <cstdarg>
#include <cstdio>
#define HIGH 1
#define LOW 0
#define OUTPUT 1
#define SERIAL_8N1 0x800001c
inline void pinMode(uint8_t, uint8_t) {}
inline void digitalWrite(uint8_t, uint8_t) {}

class HardwareSerial : public Stream {
 public:
  explicit HardwareSerial(int) {}
  void begin(unsigned long, uint32_t = SERIAL_8N1, int8_t = -1, int8_t = -1) {}
  int available() override { return 0; }
  int read() override { return -1; }
  size_t write(uint8_t) override { return 1; }
  using Stream::print;
  size_t print(char c) { return write((uint8_t)c); }
  size_t println(const char* c) { size_t n = print(c); return n + write('\n'); }
  size_t println(const String& v) { return println(v.c_str()); }
  size_t println() { return write('\n'); }
  size_t printf(const char* fmt, ...) __attribute__((format(printf, 2, 3))) {
    va_list ap; va_start(ap, fmt); int n = vprintf(fmt, ap); va_end(ap); return n < 0 ? 0 : (size_t)n;
  }
};
inline HardwareSerial Serial(0);
