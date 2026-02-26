#!/usr/bin/env python3
"""
Raspberry Pi TV control server (Flask).

Controls a Samsung TV using samsungtvws (WebSocket) for power and CEC for
HDMI input switching, with the Pi connected to the TV via Ethernet and HDMI.

Endpoints:
  GET  /tv/status        - Returns TV power state
  POST /tv/on            - Power on via WebSocket and switch HDMI input via CEC (JSON body: {"input": 1-4})
  POST /tv/off           - Turn TV off via WebSocket KEY_POWER
  POST /tv/key           - Send arbitrary key via WebSocket (JSON body: {"key": "KEY_..."})

Usage:
  python3 server.py
  python3 server.py --lan-port 8080
"""

import argparse
import json
import logging
import os
import threading
import time

import cec
import requests
from flask import Flask, jsonify, request
from samsungtvws import SamsungTVWS

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

LAN_HOST = "0.0.0.0"
LAN_PORT = 8080

TV_API_TIMEOUT = 2
POWER_POLL_INTERVAL = 2
POWER_POLL_TIMEOUT = 30
CEC_OPCODE_ACTIVE_SOURCE = 0x82

# Set log level via LOG_LEVEL env var (e.g. LOG_LEVEL=DEBUG)
log_level = os.environ.get("LOG_LEVEL", "WARNING").upper()
logging.basicConfig(level=getattr(logging, log_level, logging.WARNING))
log = logging.getLogger("tvmaster")

# ---------------------------------------------------------------------------
# Load config
# ---------------------------------------------------------------------------

config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
with open(config_path) as f:
    _config = json.load(f)

TV_IP = _config["tv_ip"]
TV_TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tv-token.txt")

cec.init()
cec_lock = threading.Lock()

# ---------------------------------------------------------------------------
# TV control
# ---------------------------------------------------------------------------


def tv_is_on() -> bool:
    """Check PowerState from the TV's REST API."""
    try:
        r = requests.get(f"http://{TV_IP}:8001/api/v2/", timeout=TV_API_TIMEOUT)
        return r.json()["device"]["PowerState"] == "on"
    except (requests.RequestException, KeyError):
        return False


def cec_set_active_source(hdmi_input: int):
    with cec_lock:
        cec.transmit(
            cec.CECDEVICE_BROADCAST,
            CEC_OPCODE_ACTIVE_SOURCE,
            bytes([hdmi_input << 4, 0x00]),
        )


def tv_on(hdmi_input: int) -> tuple[bool, str]:
    try:
        if not tv_is_on():
            log.debug("tv_on: KEY_POWER then CEC HDMI %d", hdmi_input)
            tv = SamsungTVWS(host=TV_IP, port=8002, token_file=TV_TOKEN_FILE, name="TVMaster")
            tv.send_key("KEY_POWER")
            tv.close()
            deadline = time.monotonic() + POWER_POLL_TIMEOUT
            while not tv_is_on():
                if time.monotonic() > deadline:
                    return False, "Timed out waiting for TV to turn on"
                time.sleep(POWER_POLL_INTERVAL)
        else:
            log.debug("tv_on: already on, CEC HDMI %d", hdmi_input)
        cec_set_active_source(hdmi_input)
        return True, "TV turned on"
    except Exception as e:
        log.error("tv_on failed: %s", e)
        return False, str(e)


def tv_off() -> tuple[bool, str]:
    try:
        if not tv_is_on():
            log.debug("tv_off: already off")
            return True, "TV already off"
        log.debug("tv_off: KEY_POWER")
        tv = SamsungTVWS(host=TV_IP, port=8002, token_file=TV_TOKEN_FILE, name="TVMaster")
        tv.send_key("KEY_POWER")
        tv.close()
        return True, "TV turned off"
    except Exception as e:
        log.error("tv_off failed: %s", e)
        return False, str(e)


def tv_status() -> tuple[bool, str]:
    try:
        log.debug("tv_status")
        return True, "on" if tv_is_on() else "off"
    except Exception as e:
        log.error("tv_status failed: %s", e)
        return False, str(e)


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask("tvmaster")


@app.route("/tv/status", methods=["GET"])
def tv_status_handler():
    ok, message = tv_status()
    status = 200 if ok else 500
    return jsonify(ok=ok, message=message), status


@app.route("/tv/on", methods=["POST"])
def tv_on_handler():
    if not request.is_json or "input" not in request.json:
        return jsonify(ok=False, message="Missing required 'input' field"), 400
    hdmi_input = int(request.json["input"])
    if hdmi_input not in range(1, 5):
        return jsonify(ok=False, message="'input' must be 1-4"), 400
    ok, message = tv_on(hdmi_input)
    status = 200 if ok else 500
    return jsonify(ok=ok, message=message), status


@app.route("/tv/off", methods=["POST"])
def tv_off_handler():
    ok, message = tv_off()
    status = 200 if ok else 500
    return jsonify(ok=ok, message=message), status


@app.route("/tv/key", methods=["POST"])
def tv_key_handler():
    if not request.is_json or "key" not in request.json:
        return jsonify(ok=False, message="Missing required 'key' field"), 400
    key = request.json["key"]
    try:
        tv = SamsungTVWS(host=TV_IP, port=8002, token_file=TV_TOKEN_FILE, name="TVMaster")
        tv.send_key(key)
        tv.close()
        return jsonify(ok=True, message=f"Sent {key}")
    except Exception as e:
        return jsonify(ok=False, message=str(e)), 500


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Raspberry Pi TV control server")
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
