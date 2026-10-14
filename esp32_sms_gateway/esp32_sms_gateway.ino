/*
 * MediAlabat SMS gateway — ESP32 + SIM800L
 *
 * Every few seconds this asks the MediAlabat server (over Wi-Fi) "is there a text to send?".
 * If there is, it sends it through the SIM800L and tells the server whether it worked.
 * The server keeps the queue, so nothing is lost if this board is off or the SIM has no signal —
 * messages just wait (and the staff "SMS Gateway" page shows them as pending).
 *
 * Wiring, power notes and troubleshooting: docs/ESP32_SMS_GATEWAY.md
 * Needs only the ESP32 Arduino core (WiFi + HTTPClient are built in). Board: "ESP32 Dev Module".
 */
#include <WiFi.h>
#include <HTTPClient.h>
#include "sim800_sms.h"

// ======================= EDIT THESE =======================
const char* WIFI_SSID   = "YOUR_WIFI_NAME";
const char* WIFI_PASS   = "YOUR_WIFI_PASSWORD";
const char* SERVER_BASE = "http://192.168.1.50:5000";        // the PC running MediAlabat, no trailing slash
const char* DEVICE_KEY  = "change-me-to-the-same-SMS_DEVICE_KEY";

const int      MODEM_RX_PIN  = 16;      // ESP32 RX2  <-  SIM800L TXD
const int      MODEM_TX_PIN  = 17;      // ESP32 TX2  ->  SIM800L RXD (through the resistor divider)
const int      MODEM_RST_PIN = 4;       // ESP32 GPIO4 -> SIM800L RST (active LOW)
const uint32_t MODEM_BAUD    = 9600;    // most SIM800L boards ship at 9600 (some at 115200)

const uint32_t POLL_INTERVAL_MS  = 5000;    // how often to ask the server for work
const uint32_t REG_CHECK_MS      = 30000;   // how often to re-check the modem is still on the network
const uint32_t MODEM_RETRY_MS    = 30000;   // wait this long between attempts to bring a broken modem back
// ==========================================================

HardwareSerial modemSerial(2);
Sim800 modem(modemSerial);

bool     modemReady = false;
long     lastSentId = -1;           // guards against double-sending if the server re-offers a message we already sent
int      consecutiveFailures = 0;
uint32_t lastPoll = 0, lastRegCheck = 0, lastModemTry = 0;
bool     firstModemTryDone = false;

// ---------------------------------------------------------------- helpers
String urlEncode(const String& in) {
  String out;
  const char* hex = "0123456789ABCDEF";
  for (unsigned i = 0; i < in.length(); i++) {
    char c = in[i];
    if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.') {
      out += c;
    } else {
      out += '%';
      out += hex[((uint8_t)c >> 4) & 0xF];
      out += hex[(uint8_t)c & 0xF];
    }
  }
  return out;
}

void connectWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.printf("[wifi] connecting to %s ", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) {
    delay(500);
    Serial.print('.');
  }
  if (WiFi.status() == WL_CONNECTED) Serial.printf(" ok, IP %s\n", WiFi.localIP().toString().c_str());
  else Serial.println(" failed (will retry)");
}

void resetModem() {
  pinMode(MODEM_RST_PIN, OUTPUT);
  digitalWrite(MODEM_RST_PIN, LOW);
  delay(200);
  digitalWrite(MODEM_RST_PIN, HIGH);
  Serial.println("[modem] reset, waiting for it to boot...");
  delay(6000);  // the SIM800L needs a few seconds before it answers AT
}

bool startModem() {
  String err;
  if (!modem.init(err)) {
    Serial.println("[modem] init failed: " + err);
    return false;
  }
  Serial.println("[modem] waiting for the network (can take up to a minute)...");
  if (!modem.waitRegistered(60000)) {
    Serial.println("[modem] not registered — check antenna, SIM load/registration and 2G coverage");
    return false;
  }
  Serial.printf("[modem] registered, signal %d/31\n", modem.signal());
  return true;
}

// ---------------------------------------------------------------- talking to the server
// Returns the HTTP status; on 200 the body is "<id>\t<+63 number>\t<text>".
int httpGetNext(int rssi, String& payload) {
  HTTPClient http;
  String url = String(SERVER_BASE) + "/api/sms/next";
  if (rssi >= 0 && rssi <= 31) url += "?rssi=" + String(rssi);
  http.begin(url);
  http.setTimeout(8000);
  http.addHeader("X-Device-Key", DEVICE_KEY);
  int code = http.GET();
  if (code == 200) payload = http.getString();
  http.end();
  return code;
}

bool postResult(long id, bool ok, const String& err) {
  HTTPClient http;
  http.begin(String(SERVER_BASE) + "/api/sms/" + String(id) + "/result");
  http.setTimeout(8000);
  http.addHeader("X-Device-Key", DEVICE_KEY);
  http.addHeader("Content-Type", "application/x-www-form-urlencoded");
  String body = String("ok=") + (ok ? "1" : "0");
  if (!ok) body += "&error=" + urlEncode(err);
  int code = http.POST(body);
  http.end();
  return code == 200 || code == 404;  // 404 = the server has already closed this one out; nothing more to do
}

void processOne() {
  String payload;
  int code = httpGetNext(modem.signal(), payload);
  if (code == 204) return;  // nothing to send
  if (code != 200) {
    Serial.printf("[http] poll failed (%d) — wrong SERVER_BASE / DEVICE_KEY, or server not reachable?\n", code);
    return;
  }

  payload.trim();
  int t1 = payload.indexOf('\t');
  int t2 = payload.indexOf('\t', t1 + 1);
  if (t1 < 0 || t2 < 0) {
    Serial.println("[http] unexpected reply: " + payload);
    return;
  }
  long   id     = payload.substring(0, t1).toInt();
  String number = payload.substring(t1 + 1, t2);
  String text   = payload.substring(t2 + 1);

  if (id == lastSentId) {  // we already sent this one; our earlier report must have been lost
    Serial.printf("[sms] #%ld was already sent — just re-reporting it\n", id);
    postResult(id, true, "");
    return;
  }

  Serial.printf("[sms] #%ld -> %s : %s\n", id, number.c_str(), text.c_str());
  String err;
  bool ok = modem.sendSms(number, text, err);
  if (ok) {
    lastSentId = id;
    consecutiveFailures = 0;
    Serial.printf("[sms] #%ld sent\n", id);
  } else {
    Serial.println("[sms] #" + String(id) + " FAILED: " + err);
    if (++consecutiveFailures >= 3) {  // something is wrong with the modem itself: reset it on the next loop
      modemReady = false;
      consecutiveFailures = 0;
    }
  }
  // Report back; retry a couple of times so a Wi-Fi blip doesn't leave the message stuck "in flight".
  for (int i = 0; i < 3; i++) {
    if (postResult(id, ok, err)) break;
    delay(1000);
  }
}

// ---------------------------------------------------------------- Arduino entry points
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\nMediAlabat SMS gateway starting");
  modemSerial.begin(MODEM_BAUD, SERIAL_8N1, MODEM_RX_PIN, MODEM_TX_PIN);
  connectWifi();
}

void loop() {
  connectWifi();  // returns immediately when already connected

  // Bring the modem up (first try right away, then every MODEM_RETRY_MS if it keeps failing).
  if (!modemReady && (!firstModemTryDone || millis() - lastModemTry >= MODEM_RETRY_MS)) {
    firstModemTryDone = true;
    resetModem();
    modemReady = startModem();
    lastModemTry = millis();
  }

  // Notice if the modem has dropped off the network.
  if (modemReady && millis() - lastRegCheck >= REG_CHECK_MS) {
    lastRegCheck = millis();
    if (!modem.isRegistered()) {
      Serial.println("[modem] lost the network — will reset and retry");
      modemReady = false;
    }
  }

  // Only take work when we could actually send it; otherwise it just waits safely on the server.
  if (WiFi.status() == WL_CONNECTED && modemReady && millis() - lastPoll >= POLL_INTERVAL_MS) {
    lastPoll = millis();
    processOne();
  }
  delay(50);
}
