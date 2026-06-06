# hardhack

## Thermal Overload Ticket Generator

A hardware-triggered, autonomous IT support bot that bridges a physical thermal anomaly directly to a software agent workflow — no human intervention required.

## How It Works

### 1. The Hardware Trap (Zero-Polling Detection)
During setup, a temperature threshold (e.g. 30°C) is written directly into the MCP9808 sensor's `T_UPPER` register over I2C. The STM32 microcontroller then sleeps or does other work — no polling loop.

### 2. The Hardware Trigger (Interrupt)
When ambient temperature crosses the threshold, the MCP9808 pulls its `Alert` pin LOW. This pin is wired directly to an STM32 EXTI pin, which instantly fires an Interrupt Service Routine (ISR).

### 3. The Handoff (Hardware → Software)
The ISR reads the current temperature over I2C and transmits a JSON payload over Serial (USB) to the host machine:
```json
{"status": "CRITICAL", "temp": 31.2, "location": "Server Rack A"}
```

### 4. The Agentic Action
A Python script on the host listens on the serial port. On receiving the payload, it forwards it to a Claude AI agent prompted to act as an IT manager. The agent autonomously:

- **Generates a ticket** — e.g. `URGENT: Thermal limit exceeded in Server Rack A. Current Temp: 31.2°C`
- **Proposes a solution** — e.g. `Recommend immediate inspection of HVAC unit 3`
- **Outputs the result** — to stdout, a Discord channel, or a mock Jira board

## Stack

| Layer | Technology |
|---|---|
| Firmware | C (STM32 HAL), STM32CubeIDE / PlatformIO |
| Sensor | MCP9808 over I2C |
| Host link | UART over USB CDC / pyserial |
| AI agent | Anthropic Claude API |
| Optional output | Discord webhook, mock Jira REST |
