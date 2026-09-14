#include <WiFi.h>
#include <DHT.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <Wire.h>
#include <Adafruit_BMP280.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <PubSubClient.h>

// =====================================================
// SKYGUARD AWS-001 — FINAL LIVE FAULT-INJECTION FIRMWARE
// =====================================================
// Physical buttons alter ONLY the outgoing DS18B20 telemetry copy.
// The backend is never told which button was pressed; it must infer
// the resulting fault from live telemetry using Hybrid RF v6 / quality gates.
// DHT22 humidity + reference temperature and BMP280 temperature + pressure
// remain genuine live sensor readings during every controlled test.
// =====================================================

// =====================================================
// NETWORK CONFIG
// =====================================================
const char* WIFI_SSID = "Redmi 10";
const char* WIFI_PASSWORD = "69696969";

const char* NODE_ID = "AWS_001";
const char* MQTT_BROKER = "10.228.122.188";
const int MQTT_PORT = 1883;
const char* MQTT_TOPIC = "skyguard/aws/AWS_001/telemetry";

WiFiClient mqttWifiClient;
PubSubClient mqttClient(mqttWifiClient);

// =====================================================
// PINS — EXISTING WIRING PRESERVED
// =====================================================
#define DHT_PIN 18
#define DHT_TYPE DHT22
#define DS18B20_PIN 4

#define GREEN_LED 25
#define YELLOW_LED 26
#define RED_LED 27

#define SPIKE_BUTTON 32
#define FREEZE_BUTTON 33
#define DRIFT_BUTTON 14
#define DATA_LOSS_BUTTON 13
#define CORRUPTION_BUTTON 15
// GPIO15 is an ESP32 strapping pin. Do NOT hold the corruption
// button LOW while powering on or resetting the ESP32.

#define SDA_PIN 21
#define SCL_PIN 22

// =====================================================
// OLED
// =====================================================
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET -1
#define OLED_ADDRESS 0x3C

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);
bool oledFound = false;

// =====================================================
// SENSORS
// =====================================================
DHT dht(DHT_PIN, DHT_TYPE);
OneWire oneWire(DS18B20_PIN);
DallasTemperature ds18b20(&oneWire);
Adafruit_BMP280 bmp;
bool bmpFound = false;
uint8_t bmpAddress = 0;

// =====================================================
// RUNTIME / NETWORK TIMING
// =====================================================
unsigned long sequenceNumber = 0;
unsigned long lastSend = 0;
unsigned long lastWiFiRetry = 0;
unsigned long lastMQTTRetry = 0;

const unsigned long SEND_INTERVAL = 2000;
const unsigned long WIFI_RETRY_INTERVAL = 10000;
const unsigned long MQTT_RETRY_INTERVAL = 5000;
const unsigned long BUTTON_DEBOUNCE_MS = 60;

// Explicit forward declarations keep this sketch valid even outside the
// Arduino IDE's automatic prototype-generation step.
void setNormalLED();
void setWarningLED();
void setFaultLED();

// =====================================================
// EDGEGUARD LITE V1 — LOCAL SANITY / SAFETY ASSESSMENT
// =====================================================
struct EdgeGuardResult {
  const char* localDecision;
  int riskScore;
  const char* localAction;
  const char* reasons[5];
  uint8_t reasonCount;
  uint8_t warningCount;
  uint8_t criticalCount;
};

void addEdgeWarning(EdgeGuardResult &result, const char* reason) {
  if (result.reasonCount < 5) result.reasons[result.reasonCount++] = reason;
  result.warningCount++;
}

void addEdgeCritical(EdgeGuardResult &result, const char* reason) {
  if (result.reasonCount < 5) result.reasons[result.reasonCount++] = reason;
  result.criticalCount++;
}

EdgeGuardResult evaluateEdgeGuard(
  float txDs,
  bool includeDs,
  float dhtTemp,
  float humidity,
  float bmpTemp,
  float pressure
) {
  EdgeGuardResult result;
  result.localDecision = "normal";
  result.riskScore = 0;
  result.localAction = "none";
  result.reasonCount = 0;
  result.warningCount = 0;
  result.criticalCount = 0;

  bool dsInvalid = !includeDs || isnan(txDs) || txDs == DEVICE_DISCONNECTED_C || txDs < -40.0f || txDs > 85.0f;
  if (dsInvalid) {
    addEdgeCritical(result, "DS18B20 invalid or missing");
  } else {
    if (!isnan(dhtTemp) && fabsf(txDs - dhtTemp) > 4.0f) {
      addEdgeWarning(result, "DS18B20 and DHT22 mismatch");
    }
    if (!isnan(bmpTemp) && fabsf(txDs - bmpTemp) > 4.0f) {
      addEdgeWarning(result, "DS18B20 and BMP280 mismatch");
    }
  }

  if (isnan(humidity) || humidity < 0.0f || humidity > 100.0f) {
    addEdgeCritical(result, "humidity out of range");
  }

  if (isnan(pressure) || pressure < 300.0f || pressure > 1100.0f) {
    addEdgeCritical(result, "pressure out of expected range");
  } else if (pressure < 850.0f || pressure > 1050.0f) {
    addEdgeWarning(result, "pressure out of expected range");
  }

  if (result.criticalCount > 0) {
    result.localDecision = "critical";
    result.localAction = "red_led";
    result.riskScore = 80 + ((result.criticalCount - 1) * 10) + (result.warningCount * 5);
    if (result.riskScore > 100) result.riskScore = 100;
  } else if (result.warningCount > 0) {
    result.localDecision = "warning";
    result.localAction = "yellow_led";
    result.riskScore = 40 + ((result.warningCount - 1) * 15);
    if (result.riskScore > 70) result.riskScore = 70;
  }

  return result;
}

// =====================================================
// CONTROLLED LOCAL FAULT STATE
// =====================================================
enum FaultMode {
  FAULT_NONE,
  FAULT_SPIKE,
  FAULT_FREEZE,
  FAULT_DRIFT,
  FAULT_DATA_LOSS,
  FAULT_CORRUPTION
};

FaultMode activeFault = FAULT_NONE;
unsigned long faultStartMs = 0;
unsigned long faultDurationMs = 0;
float faultBaseDsTemp = NAN;
float lastRealDsTemp = NAN;
bool lastRealDsValid = false;

struct ButtonState {
  uint8_t pin;
  FaultMode mode;
  const char* name;
  bool lastRawPressed;
  bool stablePressed;
  unsigned long lastChangeMs;
};

ButtonState buttons[] = {
  {SPIKE_BUTTON, FAULT_SPIKE, "SPIKE", false, false, 0},
  {FREEZE_BUTTON, FAULT_FREEZE, "FREEZE", false, false, 0},
  {DRIFT_BUTTON, FAULT_DRIFT, "DRIFT", false, false, 0},
  {DATA_LOSS_BUTTON, FAULT_DATA_LOSS, "DATA LOSS", false, false, 0},
  {CORRUPTION_BUTTON, FAULT_CORRUPTION, "CORRUPTION", false, false, 0},
};
const size_t BUTTON_COUNT = sizeof(buttons) / sizeof(buttons[0]);

const char* faultName(FaultMode mode) {
  switch (mode) {
    case FAULT_SPIKE: return "SPIKE";
    case FAULT_FREEZE: return "FREEZE";
    case FAULT_DRIFT: return "DRIFT";
    case FAULT_DATA_LOSS: return "LOSS";
    case FAULT_CORRUPTION: return "CORRUPT";
    default: return "NONE";
  }
}

bool faultActive() {
  return activeFault != FAULT_NONE;
}

void applyEdgeGuardLED(const EdgeGuardResult &result) {
  if (strcmp(result.localDecision, "critical") == 0) {
    setFaultLED();
  } else if (strcmp(result.localDecision, "warning") == 0 || faultActive()) {
    // Preserve the existing yellow controlled-test indicator when no edge
    // critical condition has priority.
    setWarningLED();
  } else {
    setNormalLED();
  }
}

unsigned long durationForFault(FaultMode mode) {
  switch (mode) {
    case FAULT_SPIKE: return 12000UL;
    case FAULT_FREEZE: return 90000UL;
    case FAULT_DRIFT: return 20000UL;
    case FAULT_DATA_LOSS: return 12000UL;
    case FAULT_CORRUPTION: return 8000UL;
    default: return 0UL;
  }
}

void startFault(FaultMode mode, const char* name) {
  if (faultActive()) {
    Serial.print("FAULT TEST ALREADY ACTIVE: ");
    Serial.println(faultName(activeFault));
    return;
  }

  // Every test is based on a recently validated REAL DS18B20 value.
  if (!lastRealDsValid) {
    Serial.print("Cannot start ");
    Serial.print(name);
    Serial.println(" yet: waiting for first valid DS18B20 reading.");
    return;
  }

  activeFault = mode;
  faultStartMs = millis();
  faultDurationMs = durationForFault(mode);
  faultBaseDsTemp = lastRealDsTemp;

  Serial.println();
  Serial.println("================================");
  Serial.print("CONTROLLED TEST STARTED: ");
  Serial.println(name);
  Serial.print("Captured real DS baseline: ");
  Serial.println(faultBaseDsTemp, 2);
  Serial.println("Backend receives sensor telemetry only — no fault label.");
  Serial.println("================================");
  setWarningLED();
}

void updateFaultExpiry() {
  if (!faultActive()) return;
  if (millis() - faultStartMs < faultDurationMs) return;

  Serial.print("CONTROLLED TEST COMPLETE: ");
  Serial.println(faultName(activeFault));
  activeFault = FAULT_NONE;
  faultStartMs = 0;
  faultDurationMs = 0;
  faultBaseDsTemp = NAN;

  if (WiFi.status() == WL_CONNECTED && mqttClient.connected()) {
    setNormalLED();
  }
}

void pollButtons() {
  const unsigned long now = millis();
  for (size_t i = 0; i < BUTTON_COUNT; i++) {
    ButtonState &button = buttons[i];
    bool rawPressed = digitalRead(button.pin) == LOW;

    if (rawPressed != button.lastRawPressed) {
      button.lastRawPressed = rawPressed;
      button.lastChangeMs = now;
    }

    if ((now - button.lastChangeMs) >= BUTTON_DEBOUNCE_MS && rawPressed != button.stablePressed) {
      button.stablePressed = rawPressed;
      if (button.stablePressed) {
        startFault(button.mode, button.name); // one press starts the timed test
      }
      // Release intentionally does nothing. The timed test completes automatically.
    }
  }
}

// =====================================================
// LED STATUS
// =====================================================
void setNormalLED() {
  digitalWrite(GREEN_LED, HIGH);
  digitalWrite(YELLOW_LED, LOW);
  digitalWrite(RED_LED, LOW);
}

void setWarningLED() {
  digitalWrite(GREEN_LED, LOW);
  digitalWrite(YELLOW_LED, HIGH);
  digitalWrite(RED_LED, LOW);
}

void setFaultLED() {
  digitalWrite(GREEN_LED, LOW);
  digitalWrite(YELLOW_LED, LOW);
  digitalWrite(RED_LED, HIGH);
}

// =====================================================
// WIFI / MQTT
// =====================================================
void connectWiFi() {
  Serial.print("Connecting to WiFi");
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long startAttempt = millis();

  while (WiFi.status() != WL_CONNECTED && millis() - startAttempt < 15000) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("WiFi connected!");
    Serial.print("ESP32 IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("WiFi connection FAILED. Continuing offline.");
    setWarningLED();
  }
}

void retryWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  if (lastWiFiRetry != 0 && millis() - lastWiFiRetry < WIFI_RETRY_INTERVAL) return;

  lastWiFiRetry = millis();
  Serial.println("Retrying WiFi...");
  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

void connectMQTT() {
  if (WiFi.status() != WL_CONNECTED || mqttClient.connected()) return;
  if (lastMQTTRetry != 0 && millis() - lastMQTTRetry < MQTT_RETRY_INTERVAL) return;

  lastMQTTRetry = millis();
  Serial.print("Connecting to MQTT...");
  if (mqttClient.connect("SkyGuard-AWS-001")) {
    Serial.println(" connected!");
    if (faultActive()) setWarningLED(); else setNormalLED();
  } else {
    Serial.print(" failed, state=");
    Serial.println(mqttClient.state());
    setWarningLED();
  }
}

// =====================================================
// OUTGOING TELEMETRY TRANSFORMATION
// =====================================================
void buildTelemetryDs(float realDs, float &txDs, bool &includeDs) {
  txDs = realDs;
  includeDs = true;

  if (!faultActive()) return;

  unsigned long elapsed = millis() - faultStartMs;

  switch (activeFault) {
    case FAULT_SPIKE:
      // 12 s isolated primary-temperature spike: +6 C -> +4 C -> +2 C.
      if (elapsed < 4000UL) txDs = faultBaseDsTemp + 6.0f;
      else if (elapsed < 8000UL) txDs = faultBaseDsTemp + 4.0f;
      else txDs = faultBaseDsTemp + 2.0f;
      break;

    case FAULT_FREEZE:
      // Exact held value for 90 s; references continue live. This gives the trained RF enough temporal evidence.
      txDs = faultBaseDsTemp;
      break;

    case FAULT_DRIFT: {
      // Smooth +2 C ramp over 20 s, matching the rapid-drift training regime.
      float progress = min(1.0f, (float)elapsed / (float)faultDurationMs);
      txDs = faultBaseDsTemp + (2.0f * progress);
      break;
    }

    case FAULT_DATA_LOSS:
      // Keep AWS packets alive; omit only the DS18B20 channel.
      includeDs = false;
      break;

    case FAULT_CORRUPTION:
      // Controlled DS18B20 disconnect/corruption sentinel.
      txDs = -127.0f;
      break;

    default:
      break;
  }
}

// =====================================================
// MQTT TELEMETRY
// =====================================================
void sendSensorData(
  float txDs,
  bool includeDs,
  float dhtTemp,
  float humidity,
  float bmpTemp,
  float pressure,
  const EdgeGuardResult &edgeResult
) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi disconnected - MQTT not sent");
    setWarningLED();
    return;
  }

  if (!mqttClient.connected()) connectMQTT();
  if (!mqttClient.connected()) {
    Serial.println("MQTT offline - reading not published");
    setWarningLED();
    return;
  }

  String json = "{";
  json += "\"node_id\":\"";
  json += NODE_ID;
  json += "\",";
  json += "\"sensors\":{";

  if (includeDs) {
    json += "\"ds18b20_temperature_c\":";
    json += String(txDs, 2);
    json += ",";
  }

  json += "\"dht22_temperature_c\":";
  json += String(dhtTemp, 2);
  json += ",";
  json += "\"dht22_humidity_pct\":";
  json += String(humidity, 2);
  json += ",";
  json += "\"bmp280_temperature_c\":";
  json += String(bmpTemp, 2);
  json += ",";
  json += "\"bmp280_pressure_hpa\":";
  json += String(pressure, 2);
  json += "},";

  json += "\"device\":{";
  json += "\"wifi_rssi\":";
  json += String(WiFi.RSSI());
  json += ",";
  json += "\"uptime_ms\":";
  json += String(millis());
  json += ",";
  json += "\"sequence\":";
  json += String(sequenceNumber);
  json += "},";
  json += "\"edge_ai\":{";
  json += "\"enabled\":true,";
  json += "\"version\":\"EdgeGuard Lite v1\",";
  json += "\"local_decision\":\"";
  json += edgeResult.localDecision;
  json += "\",";
  json += "\"risk_score\":";
  json += String(edgeResult.riskScore);
  json += ",\"reasons\":[";
  for (uint8_t i = 0; i < edgeResult.reasonCount; i++) {
    if (i > 0) json += ",";
    json += "\"";
    json += edgeResult.reasons[i];
    json += "\"";
  }
  json += "],\"local_action\":\"";
  json += edgeResult.localAction;
  json += "\"},";
  json += "\"source\":\"esp32\"";
  json += "}";

  bool published = mqttClient.publish(MQTT_TOPIC, json.c_str());

  if (published) {
    Serial.println("MQTT publish: OK");
    applyEdgeGuardLED(edgeResult);
  } else {
    Serial.println("MQTT publish: FAILED");
    setWarningLED();
  }

  Serial.print("MQTT topic: ");
  Serial.println(MQTT_TOPIC);
  sequenceNumber++;
}

// =====================================================
// OLED
// =====================================================
void updateOLED(float temperature, float humidity, float pressure) {
  if (!oledFound) return;

  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.setTextSize(1);
  display.setCursor(0, 0);
  display.println("SKYGUARD AWS-001");
  display.drawLine(0, 10, 127, 10, SSD1306_WHITE);

  display.setCursor(0, 15);
  display.print("Temp: ");
  display.print(temperature, 1);
  display.println(" C");
  display.print("Hum : ");
  display.print(humidity, 1);
  display.println(" %");
  display.print("Pres: ");
  display.print(pressure, 1);
  display.println(" hPa");

  display.print("Link: ");
  display.println((WiFi.status() == WL_CONNECTED && mqttClient.connected()) ? "ONLINE" : "OFFLINE");

  display.setCursor(0, 54);
  if (faultActive()) {
    display.print("TEST: ");
    display.println(faultName(activeFault));
  } else {
    display.print("SEQ: ");
    display.println(sequenceNumber);
  }
  display.display();
}

// =====================================================
// SETUP
// =====================================================
void setup() {
  Serial.begin(115200);
  delay(1000);

  pinMode(GREEN_LED, OUTPUT);
  pinMode(YELLOW_LED, OUTPUT);
  pinMode(RED_LED, OUTPUT);
  setWarningLED();

  pinMode(SPIKE_BUTTON, INPUT_PULLUP);
  pinMode(FREEZE_BUTTON, INPUT_PULLUP);
  pinMode(DRIFT_BUTTON, INPUT_PULLUP);
  pinMode(DATA_LOSS_BUTTON, INPUT_PULLUP);
  pinMode(CORRUPTION_BUTTON, INPUT_PULLUP);

  dht.begin();
  ds18b20.begin();
  Wire.begin(SDA_PIN, SCL_PIN);

  bmpFound = bmp.begin(0x76);
  if (bmpFound) {
    bmpAddress = 0x76;
  } else {
    bmpFound = bmp.begin(0x77);
    if (bmpFound) bmpAddress = 0x77;
  }

  if (bmpFound) {
    bmp.setSampling(
      Adafruit_BMP280::MODE_NORMAL,
      Adafruit_BMP280::SAMPLING_X2,
      Adafruit_BMP280::SAMPLING_X16,
      Adafruit_BMP280::FILTER_X16,
      Adafruit_BMP280::STANDBY_MS_500
    );
  }

  oledFound = display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDRESS);

  Serial.println();
  Serial.println("===============================");
  Serial.println("SKYGUARD AWS-001 FINAL STARTING");
  Serial.println("===============================");
  Serial.print("BMP280: ");
  if (bmpFound) {
    Serial.print("OK at 0x");
    if (bmpAddress < 16) Serial.print("0");
    Serial.println(bmpAddress, HEX);
  } else {
    Serial.println("NOT FOUND");
  }
  Serial.print("OLED: ");
  Serial.println(oledFound ? "OK" : "NOT FOUND");

  connectWiFi();
  mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
  mqttClient.setBufferSize(1024);
  connectMQTT();
  if (mqttClient.connected()) setNormalLED();
}

// =====================================================
// LOOP
// =====================================================
void loop() {
  retryWiFi();
  if (WiFi.status() == WL_CONNECTED && !mqttClient.connected()) connectMQTT();
  if (mqttClient.connected()) mqttClient.loop();

  updateFaultExpiry();
  pollButtons();

  if (millis() - lastSend >= SEND_INTERVAL) {
    lastSend = millis();

    // ----- Read REAL physical sensors first -----
    ds18b20.requestTemperatures();
    float realDsTemp = ds18b20.getTempCByIndex(0);
    float realDhtTemp = dht.readTemperature();
    float realHumidity = dht.readHumidity();

    float realBmpTemp = NAN;
    float realPressure = NAN;
    if (bmpFound) {
      realBmpTemp = bmp.readTemperature();
      realPressure = bmp.readPressure() / 100.0f;
    }

    bool referenceSensorsValid =
      !isnan(realDhtTemp) &&
      !isnan(realHumidity) &&
      !isnan(realBmpTemp) &&
      !isnan(realPressure);
    bool ds18b20Available =
      !isnan(realDsTemp) && realDsTemp != DEVICE_DISCONNECTED_C;

    Serial.println("------------------------");
    Serial.print("REAL DS18B20: "); Serial.println(realDsTemp, 2);
    Serial.print("REAL DHT22 T: "); Serial.println(realDhtTemp, 2);
    Serial.print("REAL Humidity: "); Serial.println(realHumidity, 2);
    Serial.print("REAL BMP280 T: "); Serial.println(realBmpTemp, 2);
    Serial.print("REAL Pressure: "); Serial.println(realPressure, 2);

    if (!referenceSensorsValid) {
      Serial.println("REFERENCE SENSOR READ ERROR - controlled injection NOT applied");
      setFaultLED();
      delay(30);
      return;
    }

    if (ds18b20Available) {
      lastRealDsTemp = realDsTemp;
      lastRealDsValid = true;
    } else {
      Serial.println("DS18B20 unavailable - publishing EdgeGuard critical status");
    }

    // ----- Make a telemetry copy, then alter only that copy -----
    float txDsTemp = realDsTemp;
    bool includeDs = ds18b20Available;
    if (ds18b20Available) {
      buildTelemetryDs(realDsTemp, txDsTemp, includeDs);
    }

    Serial.print("TEST MODE: "); Serial.println(faultName(activeFault));
    Serial.print("TX DS18B20: ");
    if (includeDs) Serial.println(txDsTemp, 2); else Serial.println("OMITTED");

    // EdgeGuard evaluates the exact values about to be published. It does not
    // alter sensor readings, fault controls, MQTT routing, or cloud inference.
    EdgeGuardResult edgeResult = evaluateEdgeGuard(
      txDsTemp,
      includeDs,
      realDhtTemp,
      realHumidity,
      realBmpTemp,
      realPressure
    );
    Serial.print("EDGEGUARD: "); Serial.print(edgeResult.localDecision);
    Serial.print(" | risk="); Serial.println(edgeResult.riskScore);
    applyEdgeGuardLED(edgeResult);

    // OLED intentionally shows the REAL local environment, not the injected copy.
    updateOLED(realDsTemp, realHumidity, realPressure);

    sendSensorData(
      txDsTemp,
      includeDs,
      realDhtTemp,
      realHumidity,
      realBmpTemp,
      realPressure,
      edgeResult
    );
  }

  delay(30);
}
