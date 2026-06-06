#include <Arduino.h>
#include <Wire.h>

// Nucleo G474RE: PB9 = SDA (D14), PB8 = SCL (D15)

static void print_known(uint8_t addr) {
    if (addr == 0x68) Serial.print(" <- MPU-6050");
    if (addr == 0x69) Serial.print(" <- MPU-6050 (AD0 high)");
    if (addr == 0x18) Serial.print(" <- MCP9808");
    if (addr == 0x19) Serial.print(" <- MCP9808 (A0 high)");
}

void setup() {
    Serial.begin(115200);
    while (!Serial && millis() < 3000);

    Wire.setClock(100000);  // 100 kHz standard mode
    Wire.begin();
    delay(100);

    Serial.println("\r\n=============================");
    Serial.println(" I2C Scanner — Nucleo G474RE");
    Serial.println("=============================");
    Serial.println(" SDA: PB9 (D14)  SCL: PB8 (D15)");
    Serial.println(" Scanning 0x01 - 0x7F ...\r\n");

    uint8_t found = 0;
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
        }
    }

    Serial.println();
    if (found == 0) {
        Serial.println("  No devices found.");
        Serial.println("  >> Check pull-ups (4.7k SDA+SCL to 3.3V)");
        Serial.println("  >> Check MPU-6050 AD0 pin -> GND for 0x68");
        Serial.println("  >> Check 3.3V on VCC, GND on GND");
    } else {
        Serial.print("  Scan complete: ");
        Serial.print(found);
        Serial.println(" device(s) found.");

        bool mpu  = false, mcp = false;
        // re-check addresses by scanning again (simple approach)
        for (uint8_t addr = 1; addr < 128; addr++) {
            Wire.beginTransmission(addr);
            if (Wire.endTransmission() == 0) {
                if (addr == 0x68 || addr == 0x69) mpu = true;
                if (addr == 0x18 || addr == 0x19) mcp = true;
            }
        }
        if (!mpu) Serial.println("  [WARN] MPU-6050 not detected");
        if (!mcp) Serial.println("  [WARN] MCP9808 not detected");
        if (mpu && mcp) Serial.println("  [OK]   Both sensors present. Ready for main firmware.");
    }

    Serial.println("=============================\r\n");
}

void loop() {
    // hold — reboot board to re-scan
}
