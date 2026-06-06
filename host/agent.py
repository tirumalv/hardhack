"""
agent.py — Gemini AI integration

API key: set the environment variable GEMINI_API_KEY before running server.py
  Windows PowerShell:  $env:GEMINI_API_KEY = "AIza..."
  Windows CMD:         set GEMINI_API_KEY=AIza...
  Mac/Linux:           export GEMINI_API_KEY=AIza...

Get a free key at: https://aistudio.google.com/apikey
"""

import json
import os
from typing import Generator

import google.generativeai as genai

MODEL = "gemini-2.0-flash"   # change to "gemini-2.5-pro" for deeper analysis


def _model(system: str) -> genai.GenerativeModel:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set.\n"
            "Get a free key at https://aistudio.google.com/apikey then run:\n"
            "  $env:GEMINI_API_KEY = 'AIza...'"
        )
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(model_name=MODEL, system_instruction=system)


# ── Incident ticket (triggered by hardware alert) ─────────────────────────────

def alert_ticket(payload: dict) -> str:
    """
    Synchronous: takes a CRITICAL alert payload, returns a formatted incident ticket.
    Called from the serial-reader thread when temperature exceeds threshold.
    """
    temp     = payload.get("temp", "?")
    threshold = payload.get("threshold", 35.0)
    location  = payload.get("location", "Unknown")
    recent    = payload.get("recent_temps", [])

    # Classify severity by how far above threshold
    try:
        excess = float(temp) - float(threshold)
        if excess >= 5:
            severity = "CRITICAL"
        elif excess >= 2:
            severity = "HIGH"
        else:
            severity = "MEDIUM"
    except (TypeError, ValueError):
        severity = "HIGH"

    system = (
        "You are an automated data-centre incident management system.\n"
        "Generate a structured IT incident ticket for a thermal overtemperature event.\n\n"
        "The ticket MUST include the following sections in order:\n\n"
        "TICKET #<auto-id>\n"
        "Severity: <CRITICAL | HIGH | MEDIUM>\n"
        "Title: <one-line summary>\n"
        "Affected Asset: <location>\n"
        "Current Temp: <value> °C  |  Threshold: <value> °C  |  Excess: <delta> °C\n"
        "Timestamp: <ISO-8601>\n\n"
        "Description:\n"
        "<2-3 sentences describing the thermal event and potential impact on hardware>\n\n"
        "Immediate Actions (execute in order):\n"
        "1. <first action>\n"
        "2. <second action>\n"
        "...\n\n"
        "Compute Workload Actions:\n"
        "- List specific steps to reduce compute load and manage temperature:\n"
        "  • Which workloads to throttle or migrate\n"
        "  • CPU/GPU power capping instructions\n"
        "  • VM/container migration recommendations\n"
        "  • Which non-critical services to suspend\n\n"
        "Cooling Actions:\n"
        "- Steps to improve airflow and cooling in the affected rack\n\n"
        "Escalation: <who to notify and when>\n"
        "Resolution Criteria: <what temperature/condition marks this ticket resolved>\n"
    )

    user_msg = (
        f"Thermal alert received:\n"
        f"  Location : {location}\n"
        f"  Temperature: {temp} °C\n"
        f"  Threshold  : {threshold} °C\n"
        f"  Severity   : {severity}\n"
        f"  Recent temps (last 10 readings): {recent}\n\n"
        "Generate the incident ticket with full compute workload reduction steps."
    )

    model = _model(system)
    response = model.generate_content(user_msg)
    return response.text


# ── On-demand analysis (streaming, called from FastAPI) ───────────────────────

def analyze_stream(samples: list, prompt: str) -> Generator[str, None, None]:
    """
    Yields text chunks. Wrap in FastAPI StreamingResponse.
    """
    def _stats(key: str):
        vals = [s[key] for s in samples if key in s]
        if not vals:
            return None
        return {"min": round(min(vals), 4),
                "max": round(max(vals), 4),
                "mean": round(sum(vals) / len(vals), 4)}

    keys = ("te",)
    stats = {k: _stats(k) for k in keys if _stats(k)}

    display = samples[-80:]

    system = (
        "You are an expert data-centre thermal management system.\n"
        "Sensor: MCP9808 temperature sensor (±0.0625 °C accuracy) monitoring inlet air temperature.\n"
        "Field: te = inlet temperature in °C\n"
        "ASHRAE thresholds: Normal < 27 °C | Warning 27-35 °C | Critical > 35 °C\n\n"
        "Respond with concise bullet-point insights. Be quantitative. Flag anomalies. "
        "Recommend specific remediation actions when temperature is elevated."
    )

    user_msg = (
        f"Dataset: {len(samples)} temperature samples. Statistical summary:\n"
        f"{json.dumps(stats, indent=2)}\n\n"
        f"Last {len(display)} samples:\n"
        f"{json.dumps(display, indent=2)}\n\n"
        f"User request: {prompt}"
    )

    def _gen():
        try:
            model = _model(system)
            response = model.generate_content(user_msg, stream=True)
            for chunk in response:
                if chunk.text:
                    yield chunk.text
        except RuntimeError as e:
            yield f"\n[Config Error: {e}]"
        except Exception as e:
            yield f"\n[Gemini Error: {e}]"

    return _gen()


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--test", metavar="JSON",
                        help="Feed a test alert payload directly (no serial needed)")
    args = parser.parse_args()

    if args.test:
        payload = json.loads(args.test)
        ticket = alert_ticket(payload)
        print(ticket)
    else:
        print("Usage:")
        print('  python agent.py --test \'{"status":"CRITICAL","temp":31.2,"location":"Server Rack A"}\'')
        print()
        print("Make sure GEMINI_API_KEY is set first.")
