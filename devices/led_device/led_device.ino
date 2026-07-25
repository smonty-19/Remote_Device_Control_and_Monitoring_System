#include <WiFi.h>
#include <ArduinoJson.h>
#include "config.h"

const char* ssid = "YOUR_WIFI";
const char* password = "YOUR_PASSWORD";
const char* server_ip = "YOUR_SERVER_IP";
const int port = 9000;

WiFiClient client;

String device_id = "led1";
String device_type = "LED";
String token = "iot-secret-2026";
String led_state = "OFF";

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

void sendStatus(const String& cmd_id, bool success, const String& message) {
  StaticJsonDocument<384> doc;
  doc["msg_type"] = "STATUS";
  doc["device_id"] = device_id;
  doc["cmd_id"] = cmd_id;
  doc["success"] = success;
  doc["message"] = message;

  JsonObject state = doc.createNestedObject("state");
  state["led"] = led_state;

  sendJSON(doc);
}

void sendHeartbeatAck() {
  StaticJsonDocument<256> doc;
  doc["msg_type"] = "HEARTBEAT_ACK";
  doc["device_id"] = device_id;
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

    if (action == "TURN_ON") {
      digitalWrite(LED_PIN, HIGH);
      led_state = "ON";
      message = "turned on";
    } else if (action == "TURN_OFF") {
      digitalWrite(LED_PIN, LOW);
      led_state = "OFF";
      message = "turned off";
    } else if (action == "TOGGLE") {
      if (led_state == "ON") {
        digitalWrite(LED_PIN, LOW);
        led_state = "OFF";
      } else {
        digitalWrite(LED_PIN, HIGH);
        led_state = "ON";
      }
      message = "toggled";
    } else {
      success = false;
      message = "unsupported action";
    }

    sendStatus(cmd_id, success, message);
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

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

  delay(10);
}