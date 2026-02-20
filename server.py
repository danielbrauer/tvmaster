#!/usr/bin/env python3
"""
Raspberry Pi CEC TV control server (Flask).

Exposes TV power control via HDMI-CEC as HTTP endpoints on the local network.

Endpoints:
  GET  /tv/status        - Returns TV power state
  POST /tv/on            - Turn TV on via CEC (optional JSON body: {"input": 1-4})
  POST /tv/off           - Turn TV off via CEC

Usage:
  python3 server.py
  python3 server.py --lan-port 8080
"""

import argparse
import logging
import os
import subprocess

from flask import Flask, jsonify, request

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

LAN_HOST = "0.0.0.0"
LAN_PORT = 8080

CEC_DEVICE = "0"  # CEC logical address for TV is typically 0

# Set log level via LOG_LEVEL env var (e.g. LOG_LEVEL=DEBUG)
log_level = os.environ.get("LOG_LEVEL", "WARNING").upper()
logging.basicConfig(level=getattr(logging, log_level, logging.WARNING))
log = logging.getLogger("tvmaster")

# ---------------------------------------------------------------------------
# CEC helpers
# ---------------------------------------------------------------------------


def cec_send(command: str) -> tuple[bool, str]:
    """Send a command via cec-client. Returns (success, output)."""
    log.debug("cec_send: %s", command)
    try:
        result = subprocess.run(
            ["cec-client", "-s", "-d", "1"],
            input=command + "\n",
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            log.debug("cec-client failed (rc=%d): stdout=%s stderr=%s",
                       result.returncode, result.stdout.strip(), result.stderr.strip())
        return result.returncode == 0, result.stdout.strip()
    except FileNotFoundError:
        log.debug("cec-client not found")
        return False, "cec-client not found"
    except subprocess.TimeoutExpired:
        log.debug("cec-client timed out")
        return False, "cec-client timed out"


def switch_input(hdmi_input: int) -> tuple[bool, str]:
    """Switch the TV to the given HDMI input (1-4)."""
    physical_address = f"{hdmi_input}0:00"
    return cec_send(f"tx 1F:82:{physical_address}")


def tv_on(hdmi_input: int | None = None) -> tuple[bool, str]:
    ok, output = cec_send(f"on {CEC_DEVICE}")
    if ok and hdmi_input is not None:
        switch_input(hdmi_input)
    return ok, "TV turned on" if ok else output


def tv_off() -> tuple[bool, str]:
    # Switch to this device first — standby is ignored if the Pi isn't the active source
    cec_send("as")
    ok, output = cec_send(f"standby {CEC_DEVICE}")
    return ok, "TV turned off" if ok else output


def tv_status() -> tuple[bool, str]:
    """Query TV power status. Returns (success, status_string)."""
    ok, output = cec_send(f"pow {CEC_DEVICE}")
    if not ok:
        return False, output
    for line in output.splitlines():
        lower = line.lower()
        if "power status:" in lower:
            if "on" in lower.split("power status:")[-1]:
                return True, "on"
            else:
                return True, "off"
    return False, f"could not parse power status: {output}"


# ---------------------------------------------------------------------------
# Flask app (TV/CEC — network-accessible)
# ---------------------------------------------------------------------------

app = Flask("tvmaster")


@app.route("/tv/status", methods=["GET"])
def tv_status_handler():
    ok, message = tv_status()
    status = 200 if ok else 500
    return jsonify(ok=ok, message=message), status


@app.route("/tv/on", methods=["POST"])
def tv_on_handler():
    hdmi_input = request.json.get("input") if request.is_json else None
    ok, message = tv_on(hdmi_input)
    status = 200 if ok else 500
    return jsonify(ok=ok, message=message), status


@app.route("/tv/off", methods=["POST"])
def tv_off_handler():
    ok, message = tv_off()
    status = 200 if ok else 500
    return jsonify(ok=ok, message=message), status


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Raspberry Pi CEC TV control server")
    parser.add_argument(
        "--lan-port",
        type=int,
        default=LAN_PORT,
        help=f"LAN-facing port (default {LAN_PORT})",
    )
    parser.add_argument(
        "--lan-host",
        default=LAN_HOST,
        help=f"LAN bind address (default {LAN_HOST})",
    )
    args = parser.parse_args()

    print(f"LAN app: {args.lan_host}:{args.lan_port}")

    try:
        app.run(host=args.lan_host, port=args.lan_port, use_reloader=False)
    except KeyboardInterrupt:
        print("\nShutting down")


if __name__ == "__main__":
    main()
