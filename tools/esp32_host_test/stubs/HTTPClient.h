#pragma once
#include <Arduino.h>
class HTTPClient {
 public:
  bool begin(String) { return true; }
  void setTimeout(uint16_t) {}
  void addHeader(const String&, const String&, bool = false, bool = false) {}
  int GET() { return -1; }
  int POST(String) { return -1; }
  String getString() { return String(); }
  void end() {}
};
