#include <WiFi.h>
#include <ArduinoJson.h>
#include "config.h"

const char* ssid = "YOUR_WIFI";
const char* password = "YOUR_PASSWORD";
const char* server_ip = "YOUR_SERVER_IP";
const int port = 9000;

WiFiClient client;

String device_id = "temp1";
String device_type = "TEMP_SENSOR";
String token = "iot-secret-2026";

unsigned long lastStatus = 0;
unsigned long statusInterval = 2000;

void sendJSON(JsonDocument &doc) {
  String out;
  serializeJson(doc, out);
  client.print(out);
  client.print("\n");
}

void sendAuth() {
  StaticJsonDocument<256> doc;
  doc["msg_type"] = "AUTH";
  doc["device_id"] = device_id;
  doc["device_type"] = device_type;
  doc["token"] = token;
  sendJSON(doc);
}

void sendAck(const String& cmd_id) {
  StaticJsonDocument<256> doc;
  doc["msg_type"] = "RECEIVED_ACK";
  doc["cmd_id"] = cmd_id;
  doc["device_id"] = device_id;
  sendJSON(doc);
}

void sendHeartbeatAck() {
  StaticJsonDocument<256> doc;
  doc["msg_type"] = "HEARTBEAT_ACK";
  doc["device_id"] = device_id;
  sendJSON(doc);
}

void sendStatus(const String& cmd_id, bool success, const String& message) {
  StaticJsonDocument<384> doc;
  doc["msg_type"] = "STATUS";
  doc["device_id"] = device_id;
  doc["cmd_id"] = cmd_id;
  doc["success"] = success;
  doc["message"] = message;

  JsonObject state = doc.createNestedObject("state");
  int raw = analogRead(TEMP_SENSOR_PIN);
  float celsius = (raw / 4095.0) * 100.0;
  state["temperature_raw"] = raw;
  state["temperature_c"] = celsius;

  sendJSON(doc);
}

void sendPeriodicStatus() {
  StaticJsonDocument<384> doc;
  doc["msg_type"] = "STATUS";
  doc["device_id"] = device_id;
  doc["success"] = true;
  doc["message"] = "periodic reading";

  JsonObject state = doc.createNestedObject("state");
  int raw = analogRead(TEMP_SENSOR_PIN);
  float celsius = (raw / 4095.0) * 100.0;
  state["temperature_raw"] = raw;
  state["temperature_c"] = celsius;

  sendJSON(doc);
}

void ensureWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;

  WiFi.disconnect();
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
  }
}

void ensureServer() {
  if (client.connected()) return;

  while (!client.connect(server_ip, port)) {
    delay(1000);
  }
  sendAuth();
}

void handleMessage(String msg) {
  StaticJsonDocument<512> doc;
  DeserializationError err = deserializeJson(doc, msg);
  if (err) return;

  String type = doc["msg_type"] | "";

  if (type == "HEARTBEAT") {
    sendHeartbeatAck();
    return;
  }

  if (type == "COMMAND") {
    String cmd_id = doc["cmd_id"] | "";
    String action = doc["action"] | "";

    sendAck(cmd_id);

    bool success = true;
    String message = "ok";

    if (action == "READ_NOW") {
      message = "reading sent";
    } else if (action == "SET_INTERVAL") {
      JsonObject params = doc["params"].as<JsonObject>();
      if (params.containsKey("seconds")) {
        float sec = params["seconds"].as<float>();
        if (sec < 0.5) sec = 0.5;
        statusInterval = (unsigned long)(sec * 1000.0);
        message = "interval updated";
      } else {
        success = false;
        message = "missing seconds parameter";
      }
    } else {
      message = "sampled reading for action";
    }

    sendStatus(cmd_id, success, message);
  }
}

void setup() {
  Serial.begin(115200);
  WiFi.mode(WIFI_STA);
  ensureWiFi();
  ensureServer();
}

void loop() {
  ensureWiFi();
  ensureServer();

  while (client.available()) {
    String msg = client.readStringUntil('\n');
    msg.trim();
    if (msg.length() > 0) {
      handleMessage(msg);
    }
  }

  if (millis() - lastStatus >= statusInterval) {
    sendPeriodicStatus();
    lastStatus = millis();
  }

  delay(10);
}