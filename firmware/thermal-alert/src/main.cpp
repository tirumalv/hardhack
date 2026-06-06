#include <Arduino.h>
#include <Wire.h>

// MPU-6050 registers
#define MPU_ADDR       0x68
#define REG_PWR_MGMT   0x6B
#define REG_TEMP_H     0x41
#define REG_TEMP_L     0x42

#define TEMP_THRESHOLD 30.0f   // degrees C
#define POLL_MS        1000    // read every 1 second
#define LOCATION       "Server Rack A"

static float read_mpu_temp() {
    Wire.beginTransmission(MPU_ADDR);
    Wire.write(REG_TEMP_H);
    Wire.endTransmission(false);
    Wire.requestFrom(MPU_ADDR, 2);

    if (Wire.available() < 2) return -999.0f;

    int16_t raw = (Wire.read() << 8) | Wire.read();
    // MPU-6050 datasheet formula
    return (raw / 340.0f) + 36.53f;
}

static void send_alert(float temp) {
    // JSON payload for the Python host listener
    Serial.print("{\"status\":\"CRITICAL\",\"temp\":");
    Serial.print(temp, 1);
    Serial.print(",\"location\":\"");
    Serial.print(LOCATION);
    Serial.println("\"}");
}

void setup() {
    Serial.begin(115200);
    while (!Serial && millis() < 3000);

    pinMode(PB9, INPUT_PULLUP);
    pinMode(PB8, INPUT_PULLUP);
    Wire.setSDA(PB9);
    Wire.setSCL(PB8);
    Wire.setClock(100000);
    Wire.begin();

    // Wake MPU-6050 — it starts in sleep mode
    Wire.beginTransmission(MPU_ADDR);
    Wire.write(REG_PWR_MGMT);
    Wire.write(0x00);
    Wire.endTransmission();
    delay(100);

    Serial.println("[BOOT] Thermal alert firmware ready");
    Serial.print("[CFG]  Threshold: ");
    Serial.print(TEMP_THRESHOLD);
    Serial.println(" C");
    Serial.print("[CFG]  Location: ");
    Serial.println(LOCATION);
    Serial.println("[INFO] Monitoring...");
}

void loop() {
    float temp = read_mpu_temp();

    if (temp == -999.0f) {
        Serial.println("[ERR] MPU-6050 not responding. Check wiring.");
    } else {
        Serial.print("[TEMP] ");
        Serial.print(temp, 1);
        Serial.print(" C  ->  ");

        if (temp > TEMP_THRESHOLD) {
            Serial.println("THRESHOLD EXCEEDED — sending alert");
            send_alert(temp);
        } else {
            Serial.println("OK");
        }
    }

    delay(POLL_MS);
}
