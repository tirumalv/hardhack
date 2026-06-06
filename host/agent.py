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
    Called from the serial-reader thread when the MCP9808 Alert fires.
    """
    system = (
        "You are an automated IT incident management system. "
        "When you receive a thermal alert from hardware sensors, generate a "
        "structured IT incident ticket. Format:\n\n"
        "TICKET #<auto-id>\n"
        "Severity: <CRITICAL | HIGH | MEDIUM>\n"
        "Title: <one-line summary>\n"
        "Affected Asset: <location from payload>\n"
        "Current Temp: <value> °C  |  Threshold: <value> °C\n"
        "Timestamp: <ISO-8601>\n"
        "Description: <2-3 sentence description>\n"
        "Recommended Action: <concise remediation steps>\n"
    )

    user_msg = (
        f"Hardware alert received:\n{json.dumps(payload, indent=2)}\n\n"
        "Generate the incident ticket."
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
