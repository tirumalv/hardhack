"""
server.py — Data Centre Thermal Monitor: host process

Reads JSON lines from the STM32 over USB serial, serves a real-time web
dashboard via FastAPI + WebSocket, and calls Gemini on thermal alerts or
on-demand user analysis.

Usage:
    $env:GEMINI_API_KEY = "AIza..."
    python server.py --port COM3 --baud 115200

    # Demo mode (realistic data-centre temperature simulation):
    python server.py --demo
"""

import argparse
import asyncio
import json
import os
import queue
import sys
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import serial
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse

import agent as ai_agent

# ── Config ────────────────────────────────────────────────────────────────────

BUFFER_SIZE     = 1000    # rolling sensor frame count
ALERT_THRESHOLD = 35.0    # °C — ASHRAE A2 critical limit
ALERT_COOLDOWN  = 60      # seconds between auto-generated tickets (avoid spam)
FRONTEND_PATH   = os.path.join(os.path.dirname(__file__), "..", "frontend", "index.html")


# ── Shared state ──────────────────────────────────────────────────────────────

sensor_buf: deque = deque(maxlen=BUFFER_SIZE)
latest_frame: dict | None = None
tickets: list[dict] = []
serial_raw_q: queue.Queue = queue.Queue(maxsize=500)
last_alert_time: float = 0.0    # epoch seconds of last ticket generation


# ── WebSocket manager ─────────────────────────────────────────────────────────

class WSManager:
    def __init__(self):
        self._clients: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._clients.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self._clients:
            self._clients.remove(ws)

    async def broadcast(self, obj: dict):
        msg = json.dumps(obj)
        dead = []
        for ws in list(self._clients):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    @property
    def count(self):
        return len(self._clients)


ws = WSManager()


# ── Serial reader (runs in background thread) ─────────────────────────────────

def serial_reader(port: str, baud: int, stop_event: threading.Event):
    """Continuously reads lines from serial, puts raw bytes into serial_raw_q."""
    while not stop_event.is_set():
        try:
            print(f"[serial] Opening {port} @ {baud}")
            with serial.Serial(port, baud, timeout=1) as ser:
                print(f"[serial] Connected. Streaming data…")
                buf = b""
                while not stop_event.is_set():
                    chunk = ser.read(ser.in_waiting or 1)
                    if chunk:
                        buf += chunk
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            line = line.strip()
                            if line:
                                try:
                                    serial_raw_q.put_nowait(line)
                                except queue.Full:
                                    pass
        except serial.SerialException as e:
            print(f"[serial] Error: {e} — retrying in 3s")
            time.sleep(3)


# ── Async processor (runs in event loop) ─────────────────────────────────────

async def process_serial_queue():
    """Drains serial_raw_q, updates state, broadcasts to WebSocket clients."""
    global latest_frame, last_alert_time

    loop = asyncio.get_event_loop()

    while True:
        # Pull all pending items without blocking the event loop
        batch = []
        try:
            while True:
                batch.append(serial_raw_q.get_nowait())
        except queue.Empty:
            pass

        for raw in batch:
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                # Plain-text debug lines from firmware ([TEMP], [BOOT], etc.) — log and skip
                print(f"[serial] {raw.decode(errors='replace').strip()}")
                continue

            frame["_ts"] = datetime.now(timezone.utc).isoformat()

            # Real firmware sends {"status":"CRITICAL","temp":...,"location":...}
            # Feature-pipeline firmware sends {"type":"alert/data/boot",...}
            # Normalise both into a single ftype:
            if frame.get("status") == "CRITICAL":
                ftype = "alert"
                frame.setdefault("threshold", 35.0)
            else:
                ftype = frame.get("type", "data")

            if ftype == "data":
                sensor_buf.append(frame)
                latest_frame = frame
                await ws.broadcast({"type": "sensor", "data": frame})

                # Server-side threshold check — catches demo mode and any firmware
                # that streams data frames without a separate alert message
                te = frame.get("te") or frame.get("temp")
                if te is not None and te > ALERT_THRESHOLD:
                    alert_frame = {
                        "status": "CRITICAL",
                        "temp": te,
                        "threshold": ALERT_THRESHOLD,
                        "location": frame.get("location", "Server Rack A"),
                        "_ts": frame["_ts"],
                    }
                    ftype = "alert"
                    frame = alert_frame

            elif ftype == "alert":
                print(f"[ALERT] {frame}")
                await ws.broadcast({"type": "alert", "data": frame})

                # Rate-limit ticket generation to avoid spam during sustained overtemp
                now = time.time()
                if now - last_alert_time >= ALERT_COOLDOWN:
                    last_alert_time = now
                    # Capture frame for closure
                    _frame = dict(frame)
                    _frame["recent_temps"] = [
                        s.get("te") for s in list(sensor_buf)[-10:] if "te" in s
                    ]

                    def _gen(_f=_frame):
                        try:
                            ticket_text = ai_agent.alert_ticket(_f)
                            ticket = {
                                "id": len(tickets) + 1,
                                "timestamp": _f["_ts"],
                                "payload": _f,
                                "ticket": ticket_text,
                            }
                            tickets.append(ticket)
                            asyncio.run_coroutine_threadsafe(
                                ws.broadcast({"type": "ticket", "data": ticket}),
                                loop,
                            )
                        except Exception as e:
                            print(f"[agent] ticket error: {e}")

                    threading.Thread(target=_gen, daemon=True).start()
                else:
                    remaining = int(ALERT_COOLDOWN - (now - last_alert_time))
                    print(f"[ALERT] Ticket suppressed (cooldown: {remaining}s remaining)")

            elif ftype == "boot":
                await ws.broadcast({"type": "boot", "data": frame})

        await asyncio.sleep(0.02)   # ~50 Hz drain cycle


# ── FastAPI ───────────────────────────────────────────────────────────────────

stop_event = threading.Event()
serial_thread: threading.Thread | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(process_serial_queue())
    yield
    stop_event.set()


app = FastAPI(title="Thermal Overload Dashboard", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])


@app.get("/", response_class=HTMLResponse)
async def root():
    with open(FRONTEND_PATH, encoding="utf-8") as f:
        return f.read()


@app.get("/api/status")
async def status():
    return {
        "ws_clients": ws.count,
        "buffer_size": len(sensor_buf),
        "tickets": len(tickets),
        "latest": latest_frame,
    }


@app.get("/api/tickets")
async def get_tickets():
    return tickets


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await ws.connect(websocket)
    # send snapshot immediately
    if latest_frame:
        await websocket.send_text(json.dumps({"type": "sensor", "data": latest_frame}))
    for ticket in tickets[-5:]:
        await websocket.send_text(json.dumps({"type": "ticket", "data": ticket}))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws.disconnect(websocket)


@app.post("/api/analyze")
async def analyze(request: dict):
    prompt = request.get("prompt", "Describe what is happening with this sensor data.")
    n = min(int(request.get("samples", 200)), len(sensor_buf))

    if not sensor_buf:
        return StreamingResponse(
            iter(["No data available."]),
            media_type="text/plain",
        )

    samples = list(sensor_buf)[-n:]

    try:
        gen = ai_agent.analyze_stream(samples, prompt)
    except RuntimeError as e:
        return StreamingResponse(iter([str(e)]), media_type="text/plain")

    return StreamingResponse(gen, media_type="text/plain")


@app.post("/api/simulate-alert")
async def simulate_alert():
    """Inject a test alert (demo mode only)."""
    payload = {
        "status": "CRITICAL",
        "temp": 36.4,
        "threshold": 35.0,
        "location": "Server Rack A",
    }
    serial_raw_q.put(json.dumps(payload).encode())
    return {"ok": True}


# ── Demo data generator (realistic data-centre temperatures) ──────────────────

def demo_reader(stop_event: threading.Event):
    """
    Simulates a data-centre inlet temperature sensor.
    Starts at 28 °C (slightly warm), slowly drifts upward with realistic noise,
    occasional workload spikes that push into warning/critical range.
    """
    import math, random
    t = 0.0
    # Slow upward trend: starts at 28, climbs ~0.3 °C/min, plateaus around 32-33
    trend = 28.0
    spike_active = 0       # seconds remaining in current spike
    spike_peak   = 0.0

    while not stop_event.is_set():
        # Slow upward trend — rises ~0.005 °C/s, soft-caps around 32 °C
        trend += 0.005 * (1.0 - max(0, trend - 28.0) / 6.0)

        # Small sine wave to look "real-time" (HVAC cycling effect)
        wave = math.sin(t * 0.08) * 0.4 + math.sin(t * 0.21) * 0.15

        # Random micro-noise
        noise = random.uniform(-0.12, 0.12)

        # Occasional workload spike: every 90-150 s, temp rises 3-7 °C over ~45 s
        if spike_active <= 0 and random.random() < 0.007:   # ~0.7% chance per second
            spike_active = random.randint(35, 55)
            spike_peak   = random.uniform(3.5, 7.5)

        spike_val = 0.0
        if spike_active > 0:
            progress = 1.0 - (spike_active / 45.0)
            spike_val = spike_peak * math.sin(max(0, progress) * math.pi)
            spike_active -= 1

        temp = round(trend + wave + noise + spike_val, 2)
        # Clamp to realistic range
        temp = max(26.0, min(42.0, temp))

        frame = {
            "type": "data",
            "ms": int(t * 1000),
            "te": temp,
        }
        serial_raw_q.put(json.dumps(frame).encode())
        t += 1.0
        time.sleep(1.0)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Data Centre Thermal Monitor")
    parser.add_argument("--port", default="COM3", help="Serial port (e.g. COM3 or /dev/ttyACM0)")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--web-port", type=int, default=8000, help="HTTP/WebSocket port")
    parser.add_argument("--demo", action="store_true",
                        help="Run with simulated data-centre temperature data (no board needed)")
    args = parser.parse_args()

    if args.demo:
        print("[server] DEMO mode — simulating data-centre temperature sensor.")
        t = threading.Thread(target=demo_reader, args=(stop_event,), daemon=True)
    else:
        t = threading.Thread(target=serial_reader,
                             args=(args.port, args.baud, stop_event), daemon=True)
    t.start()

    print(f"[server] Dashboard -> http://localhost:{args.web_port}")
    uvicorn.run(app, host="0.0.0.0", port=args.web_port, log_level="warning")


if __name__ == "__main__":
    main()
