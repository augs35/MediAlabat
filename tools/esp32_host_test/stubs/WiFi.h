#pragma once
#include <Arduino.h>
typedef enum { WL_IDLE_STATUS = 0, WL_CONNECTED = 3, WL_DISCONNECTED = 6 } wl_status_t;
typedef enum { WIFI_OFF = 0, WIFI_STA = 1 } wifi_mode_t;
struct IPAddress { String toString() const { return String("0.0.0.0"); } };
struct WiFiClass {
  wl_status_t status() { return WL_DISCONNECTED; }
  bool mode(wifi_mode_t) { return true; }
  wl_status_t begin(const char*, const char* = nullptr) { return WL_DISCONNECTED; }
  IPAddress localIP() { return IPAddress(); }
};
inline WiFiClass WiFi;
