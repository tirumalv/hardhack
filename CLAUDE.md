# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Thermal Overload Ticket Generator** — a hardware-triggered autonomous IT support bot. A physical temperature anomaly on an MCP9808 sensor fires a hardware interrupt on an STM32 microcontroller, which pushes a JSON alert over Serial/USB to a Python host process, which forwards it to a Claude AI agent that auto-generates a structured incident ticket.

## Architecture

The system has two distinct layers that communicate over Serial (USB):

### Firmware Layer (`firmware/`)
- Target: STM32 microcontroller (STM32CubeIDE or PlatformIO project)
- Sensor: MCP9808 temperature sensor over I2C
- The MCP9808's `T_UPPER` threshold register is written once at startup — no polling loop
- When temperature breaches the threshold, the MCP9808 pulls its `Alert` pin LOW
- That pin is wired to an STM32 EXTI pin; the voltage drop triggers an ISR immediately
- The ISR reads the current temperature via I2C and transmits a JSON payload over UART/USB:
  ```json
  {"status": "CRITICAL", "temp": 31.2, "location": "Server Rack A"}
  ```
- After transmitting, the ISR clears the MCP9808 alert flag and returns

### Host Layer (`host/`)
- Language: Python 3
- A serial listener process (`listener.py` or similar) opens the COM/tty port and blocks on incoming data
- On receiving a valid JSON payload, it invokes the AI agent pipeline
- The agent (Anthropic Claude API or LangChain wrapper) is prompted to act as an IT manager and produces:
  - A formatted incident ticket (title, severity, affected location, timestamp)
  - A recommended remediation step based on the thermal data
- Output goes to stdout and optionally to a Discord webhook or mock Jira endpoint

## Key Design Constraint

**Zero-polling on the microcontroller side.** The MCP9808 interrupt architecture means the STM32 CPU is idle (or doing other work) until the hardware threshold is crossed. Any future firmware changes must preserve this — do not introduce a `while(1)` polling loop that reads temperature on a timer.

## Expected Tech Stack

| Layer | Technology |
|---|---|
| Firmware | C (STM32 HAL), STM32CubeIDE or PlatformIO |
| Sensor protocol | I2C (MCP9808 datasheet register map) |
| Host-firmware link | UART over USB CDC / pyserial |
| AI agent | Anthropic Claude API (`anthropic` Python SDK) |
| Optional output | Discord webhook, mock Jira REST |

## Development Workflow (once code exists)

**Firmware:**
```bash
# PlatformIO (if used)
pio run                   # build
pio run --target upload   # flash to board
pio device monitor        # open serial monitor
```

**Host Python:**
```bash
pip install -r requirements.txt
python host/listener.py --port /dev/tty.usbmodem* --baud 115200
```

**Simulate a hardware trigger (no board needed):**
```bash
# Feed a test payload directly to the agent without real serial hardware
python host/agent.py --test '{"status":"CRITICAL","temp":31.2,"location":"Server Rack A"}'
```

## MCP9808 Register Quick-Reference

| Register | Address | Purpose |
|---|---|---|
| `T_UPPER` | `0x02` | Upper alert threshold (12-bit, 0.0625°C/LSB) |
| `T_LOWER` | `0x03` | Lower alert threshold |
| `T_CRIT` | `0x04` | Critical threshold |
| `T_A` | `0x05` | Ambient temperature (read-only) |
| `CONFIG` | `0x01` | Alert mode, polarity, interrupt clear |

Alert pin is active-low by default; the `ALTPOL` bit in `CONFIG` can invert it.
