#include <Arduino.h>
#include <Wire.h>

// Nucleo G474RE + GY-521 MPU-6050 breakout
// GY-521 has built-in pull-ups — no external resistors needed
// SDA: PB9 (D14)   SCL: PB8 (D15)
// VCC: 3.3V        GND: GND
// AD0: GND = 0x68, AD0: floating/3.3V = 0x69

static void print_known(uint8_t addr) {
    if (addr == 0x68) Serial.print(" <- MPU-6050 (AD0=GND)");
    if (addr == 0x69) Serial.print(" <- MPU-6050 (AD0=float/3V3)");
    if (addr == 0x18) Serial.print(" <- MCP9808");
    if (addr == 0x19) Serial.print(" <- MCP9808 (A0 high)");
}

void setup() {
    Serial.begin(115200);
    while (!Serial && millis() < 3000);

    // Explicitly set I2C pins with internal pull-ups as backup
    pinMode(PB9, INPUT_PULLUP);  // SDA
    pinMode(PB8, INPUT_PULLUP);  // SCL

    Wire.setSDA(PB9);
    Wire.setSCL(PB8);
    Wire.setClock(100000);
    Wire.begin();
    delay(200);

    Serial.println("\r\n=============================");
    Serial.println(" I2C Scanner — Nucleo G474RE");
    Serial.println(" GY-521 MPU-6050 breakout");
    Serial.println("=============================");
    Serial.println(" SDA: PB9 (D14)  SCL: PB8 (D15)");
    Serial.println(" Scanning 0x01 - 0x7F ...\r\n");

    uint8_t found = 0;
    bool mpu = false, mcp = false;

    for (uint8_t addr = 1; addr < 128; addr++) {
        Wire.beginTransmission(addr);
        uint8_t err = Wire.endTransmission();

        if (err == 0) {
            Serial.print("  [FOUND] 0x");
            if (addr < 16) Serial.print("0");
            Serial.print(addr, HEX);
            print_known(addr);
            Serial.println();
            found++;
            if (addr == 0x68 || addr == 0x69) mpu = true;
            if (addr == 0x18 || addr == 0x19) mcp = true;
        }
    }

    Serial.println();
    if (found == 0) {
        Serial.println("  No devices found. Checklist:");
        Serial.println("  [1] VCC -> 3.3V on Nucleo (not 5V)");
        Serial.println("  [2] GND -> GND");
        Serial.println("  [3] SDA -> PB9 (D14)");
        Serial.println("  [4] SCL -> PB8 (D15)");
        Serial.println("  [5] GY-521 has built-in pull-ups — no external ones needed");
        Serial.println("  [6] Try wiring AD0 -> GND to lock address at 0x68");
    } else {
        Serial.print("  Scan complete: ");
        Serial.print(found);
        Serial.println(" device(s) found.");
        if (mpu)  Serial.println("  [OK]  MPU-6050 detected");
        if (!mpu) Serial.println("  [WARN] MPU-6050 not detected");
        if (mcp)  Serial.println("  [OK]  MCP9808 detected");
        if (mpu && !mcp) Serial.println("  [INFO] Wire MCP9808 next to complete sensor suite");
    }

    Serial.println("=============================\r\n");
}

void loop() {}
