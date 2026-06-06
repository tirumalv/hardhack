#include <Arduino.h>
#include <Wire.h>

// ── Pin & I2C config ─────────────────────────────────────────────────────────
// Nucleo G474RE: PB9=SDA(D14)  PB8=SCL(D15)
// MCP9808 Alert  → PA0 (Arduino A0, any EXTI-capable pin)
// MPU6050 AD0    → GND → address 0x68

#define MCP9808_ADDR   0x18
#define MPU6050_ADDR   0x68
#define ALERT_PIN      PA0    // wired to MCP9808 Alert (active LOW)
#define TEMP_THRESHOLD 30.0f  // °C — writes into MCP9808 T_UPPER register

// ── MCP9808 registers ────────────────────────────────────────────────────────
#define MCP_CONFIG     0x01
#define MCP_T_UPPER    0x02
#define MCP_T_LOWER    0x03
#define MCP_T_CRIT     0x04
#define MCP_AMBIENT    0x05
#define MCP_MANUF_ID   0x06
#define MCP_DEVICE_ID  0x07

// ── MPU6050 registers ────────────────────────────────────────────────────────
#define MPU_PWR_MGMT   0x6B
#define MPU_ACCEL_OUT  0x3B   // burst: AX AY AZ TEMP GX GY GZ (14 bytes)
#define ACCEL_SCALE    16384.0f   // ±2g default
#define GYRO_SCALE     131.0f    // ±250 deg/s default

// ── State ────────────────────────────────────────────────────────────────────
volatile bool alert_fired = false;
volatile float alert_temp = 0.0f;

// ── Helpers ──────────────────────────────────────────────────────────────────

static void i2c_write16(uint8_t dev, uint8_t reg, uint16_t val) {
    Wire.beginTransmission(dev);
    Wire.write(reg);
    Wire.write((val >> 8) & 0xFF);
    Wire.write(val & 0xFF);
    Wire.endTransmission();
}

static uint16_t i2c_read16(uint8_t dev, uint8_t reg) {
    Wire.beginTransmission(dev);
    Wire.write(reg);
    Wire.endTransmission(false);
    Wire.requestFrom(dev, (uint8_t)2);
    uint8_t msb = Wire.read();
    uint8_t lsb = Wire.read();
    return (msb << 8) | lsb;
}

// Convert MCP9808 raw 16-bit word → °C
static float mcp_raw_to_c(uint16_t raw) {
    raw &= 0x1FFF;                  // strip boundary flags
    float t = (raw & 0x0FFF) / 16.0f;
    if (raw & 0x1000) t -= 256.0f;
    return t;
}

// Convert 0.0625 °C/LSB format for writing thresholds
static uint16_t c_to_threshold(float c) {
    int16_t t = (int16_t)(c * 16.0f);
    return (uint16_t)(t << 4) & 0x0FF0;
}

// ── MCP9808 init ─────────────────────────────────────────────────────────────

static bool mcp9808_init() {
    // Check manufacturer ID
    uint16_t mfr = i2c_read16(MCP9808_ADDR, MCP_MANUF_ID);
    if (mfr != 0x0054) return false;

    // Write T_UPPER threshold
    i2c_write16(MCP9808_ADDR, MCP_T_UPPER, c_to_threshold(TEMP_THRESHOLD));

    // CONFIG: alert output enabled, alert select = upper+crit, active-low, comparator mode
    // Bit 3 (ALTSEL) = 0 → alert on T_UPPER, T_LOWER, T_CRIT
    // Bit 2 (ALTPOL) = 0 → active-low
    // Bit 1 (ALTMOD) = 0 → comparator (not interrupt)
    // Bit 0 (ALTCNT) = 1 → enable alert output
    i2c_write16(MCP9808_ADDR, MCP_CONFIG, 0x0008);  // ALTENA=1

    return true;
}

static float mcp9808_read() {
    uint16_t raw = i2c_read16(MCP9808_ADDR, MCP_AMBIENT);
    return mcp_raw_to_c(raw);
}

// ── MPU6050 init ─────────────────────────────────────────────────────────────

static bool mpu6050_init() {
    Wire.beginTransmission(MPU6050_ADDR);
    Wire.write(MPU_PWR_MGMT);
    Wire.write(0x00);  // clear sleep bit
    if (Wire.endTransmission() != 0) return false;
    delay(10);
    return true;
}

struct IMUData { float ax, ay, az, gx, gy, gz, temp; };

static bool mpu6050_read(IMUData &d) {
    Wire.beginTransmission(MPU6050_ADDR);
    Wire.write(MPU_ACCEL_OUT);
    Wire.endTransmission(false);
    if (Wire.requestFrom(MPU6050_ADDR, (uint8_t)14) != 14) return false;

    /* Lambda capturing globals needs C++11; unroll to be safe on all STM32 toolchains */
    #define R16() ((int16_t)((Wire.read() << 8) | Wire.read()))
    d.ax   = R16() / ACCEL_SCALE;
    d.ay   = R16() / ACCEL_SCALE;
    d.az   = R16() / ACCEL_SCALE;
    d.temp = R16() / 340.0f + 36.53f;
    d.gx   = R16() / GYRO_SCALE;
    d.gy   = R16() / GYRO_SCALE;
    d.gz   = R16() / GYRO_SCALE;
    #undef R16
    return true;
}

// ── Alert ISR ────────────────────────────────────────────────────────────────

void on_alert() {
    alert_fired = true;
}

// ── Setup ────────────────────────────────────────────────────────────────────

void setup() {
    Serial.begin(115200);
    while (!Serial && millis() < 3000);

    Wire.setClock(100000);
    Wire.begin();
    delay(100);

    bool mcp_ok = mcp9808_init();
    bool mpu_ok = mpu6050_init();

    // Send boot status JSON
    Serial.print("{\"type\":\"boot\",\"mcp9808\":");
    Serial.print(mcp_ok ? "true" : "false");
    Serial.print(",\"mpu6050\":");
    Serial.print(mpu_ok ? "true" : "false");
    Serial.print(",\"threshold\":");
    Serial.print(TEMP_THRESHOLD, 1);
    Serial.println("}");

    // Configure alert pin as input (active LOW, no internal pull)
    pinMode(ALERT_PIN, INPUT);
    attachInterrupt(digitalPinToInterrupt(ALERT_PIN), on_alert, FALLING);
}

// ── Loop ─────────────────────────────────────────────────────────────────────

void loop() {
    static uint32_t last_ms = 0;

    // ── Handle alert (interrupt-driven) ──────────────────────────────────────
    if (alert_fired) {
        alert_fired = false;
        float t = mcp9808_read();
        Serial.print("{\"type\":\"alert\",\"status\":\"CRITICAL\","
                     "\"temp\":");
        Serial.print(t, 2);
        Serial.print(",\"threshold\":");
        Serial.print(TEMP_THRESHOLD, 1);
        Serial.println(",\"location\":\"Server Rack A\"}");
    }

    // ── Regular 10 Hz data stream ─────────────────────────────────────────────
    uint32_t now = millis();
    if (now - last_ms < 100) return;
    last_ms = now;

    float ext_temp = mcp9808_read();
    IMUData imu;
    if (!mpu6050_read(imu)) return;

    // Compact JSON (one line, newline-terminated for easy host parsing)
    Serial.print("{\"type\":\"data\","
                 "\"ms\":");
    Serial.print(now);
    Serial.print(",\"ax\":");  Serial.print(imu.ax, 4);
    Serial.print(",\"ay\":");  Serial.print(imu.ay, 4);
    Serial.print(",\"az\":");  Serial.print(imu.az, 4);
    Serial.print(",\"gx\":");  Serial.print(imu.gx, 3);
    Serial.print(",\"gy\":");  Serial.print(imu.gy, 3);
    Serial.print(",\"gz\":");  Serial.print(imu.gz, 3);
    Serial.print(",\"ti\":");  Serial.print(imu.temp, 2);
    Serial.print(",\"te\":");  Serial.print(ext_temp, 2);
    Serial.println("}");
}
