import json
import ssl
import paho.mqtt.client as mqtt

from app.live_inference import SkyGuardLiveClassifier


# =====================================================
# CONFIG
# =====================================================

MODEL_PATH = "app/models/skyguard_pytorch_multiclass_v3.pt"

MQTT_BROKER = "2a39147c05e34fca81922447ecc81a83.s1.eu.hivemq.cloud"
MQTT_PORT = 8883

MQTT_TOPIC = "skyguard/aws/AWS_001/telemetry"

MQTT_USER = "skyguard"

# Put your HiveMQ password here locally
MQTT_PASSWORD = "12345G@urav"



# =====================================================
# LOAD PYTORCH MODEL
# =====================================================

print()
print("========================================")
print("SKYGUARD LIVE PYTORCH MQTT TEST")
print("========================================")
print()

print("Loading PyTorch model...")

detector = SkyGuardLiveClassifier(MODEL_PATH)

print("Model loaded successfully.")
print("Classes:", detector.classes)
print("Sequence length:", detector.seq_len)
print()


# =====================================================
# MQTT CONNECT CALLBACK
# =====================================================

def on_connect(
    client,
    userdata,
    flags,
    reason_code,
    properties
):

    if reason_code == 0:

        print("MQTT connected successfully.")
        print("Subscribed to:", MQTT_TOPIC)
        print()

        client.subscribe(MQTT_TOPIC)

    else:

        print("MQTT connection failed.")
        print("Reason code:", reason_code)


# =====================================================
# MQTT MESSAGE CALLBACK
# =====================================================

def on_message(client, userdata, msg):

    try:

        # -------------------------------------------------
        # Decode MQTT JSON
        # -------------------------------------------------

        payload = msg.payload.decode(
            "utf-8"
        )

        data = json.loads(
            payload
        )

        sensors = data.get(
            "sensors",
            {}
        )


        # -------------------------------------------------
        # BUILD MODEL INPUT
        # -------------------------------------------------

        reading = {

            "ds18b20_temperature_c":
                sensors.get(
                    "ds18b20_temperature_c"
                ),

            "dht22_temperature_c":
                sensors.get(
                    "dht22_temperature_c"
                ),

            "dht22_humidity_pct":
                sensors.get(
                    "dht22_humidity_pct"
                ),

            "bmp280_temperature_c":
                sensors.get(
                    "bmp280_temperature_c"
                ),

            "bmp280_pressure_hpa":
                sensors.get(
                    "bmp280_pressure_hpa"
                ),
        }


        # -------------------------------------------------
        # PYTORCH INFERENCE
        # -------------------------------------------------

        result = detector.update(
            reading
        )


        # -------------------------------------------------
        # PRINT SENSOR VALUES
        # -------------------------------------------------

        print("----------------------------------------")

        print(
            "NODE:",
            data.get(
                "node_id",
                "UNKNOWN"
            )
        )

        print(
            "DS18B20:",
            reading[
                "ds18b20_temperature_c"
            ]
        )

        print(
            "DHT22 Temp:",
            reading[
                "dht22_temperature_c"
            ]
        )

        print(
            "Humidity:",
            reading[
                "dht22_humidity_pct"
            ]
        )

        print(
            "BMP280 Temp:",
            reading[
                "bmp280_temperature_c"
            ]
        )

        print(
            "Pressure:",
            reading[
                "bmp280_pressure_hpa"
            ]
        )

        print()


        # -------------------------------------------------
        # MODEL WARMUP
        # -------------------------------------------------

        if not result.get("ready"):

            print(
                "AI warming up...",
                result.get(
                    "needed"
                ),
                "readings remaining"
            )

            print()

            return


        # -------------------------------------------------
        # MODEL RESULTS
        # -------------------------------------------------

        prediction = result[
            "prediction"
        ]

        confidence = result[
            "confidence"
        ]

        confirmed = result[
            "confirmed"
        ]

        confirmed_fault = result.get(
            "confirmed_fault"
        )


        print(
            "AI Prediction:",
            prediction.upper()
        )

        print(
            "Confidence:",
            f"{confidence * 100:.1f}%"
        )


        # =================================================
        # STABLE CONFIRMED STATE
        # =================================================

        print(
            "Confirmed:",
            "YES"
            if confirmed
            else "NO"
        )

        print(
            "Confirmed Fault:",
            confirmed_fault.upper()
            if confirmed_fault
            else "NONE"
        )


        print(
            "Same prediction windows:",
            result.get(
                "same_prediction_windows",
                0
            )
        )


        # -------------------------------------------------
        # RECOVERY STATUS
        # -------------------------------------------------

        normal_recovery_count = result.get(
            "normal_recovery_count",
            0
        )

        normal_recovery_required = result.get(
            "normal_recovery_required",
            3
        )


        if confirmed_fault:

            print(
                "Normal recovery:",
                f"{normal_recovery_count}"
                f"/{normal_recovery_required}"
            )


        print()


        # -------------------------------------------------
        # CLASS PROBABILITIES
        # -------------------------------------------------

        print("Probabilities:")

        for cls, prob in result[
            "probabilities"
        ].items():

            print(
                f"  {cls:12s}: "
                f"{prob * 100:6.2f}%"
            )

        print()


        # =================================================
        # HUMAN-READABLE STATUS
        # =================================================

        if confirmed_fault:

            print(
                ">>> SYSTEM ALERT:",
                confirmed_fault.upper(),
                "CONFIRMED"
            )

        elif prediction != "normal":

            print(
                ">>> STATUS:",
                prediction.upper(),
                "SUSPECTED"
            )

        else:

            print(
                ">>> SYSTEM STATUS: NORMAL"
            )

        print()


    except json.JSONDecodeError:

        print(
            "ERROR: Invalid JSON received from MQTT."
        )


    except Exception as e:

        print(
            "ERROR processing MQTT message:"
        )

        print(e)


# =====================================================
# MQTT CLIENT
# =====================================================

client = mqtt.Client(
    callback_api_version=
        mqtt.CallbackAPIVersion.VERSION2
)


client.username_pw_set(
    MQTT_USER,
    MQTT_PASSWORD
)


# HiveMQ Cloud TLS
client.tls_set(
    cert_reqs=
        ssl.CERT_REQUIRED
)


client.on_connect = on_connect

client.on_message = on_message


# =====================================================
# CONNECT
# =====================================================

print(
    "Connecting to HiveMQ Cloud..."
)


try:

    client.connect(
        MQTT_BROKER,
        MQTT_PORT,
        keepalive=60
    )

    client.loop_forever()


except KeyboardInterrupt:

    print()
    print(
        "Stopping SkyGuard live AI test..."
    )

    client.disconnect()


except Exception as e:

    print()
    print(
        "MQTT connection/runtime error:"
    )

    print(e)