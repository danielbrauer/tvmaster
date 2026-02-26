#!/usr/bin/env python3
"""
Raspberry Pi TV control server (Flask).

Controls a Samsung TV using samsungtvws (WebSocket) for power, WoL for
waking from deep sleep, and CEC for HDMI input switching, with the Pi
connected to the TV via Ethernet and HDMI.

Endpoints:
  GET  /tv/status        - Returns TV power state
  POST /tv/on            - Power on via WoL/WebSocket and switch HDMI input via CEC (JSON body: {"source": "<name>"})
  POST /tv/off           - Turn TV off via WebSocket KEY_POWER (JSON body: {"source": "<name>"})
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
from wakeonlan import send_magic_packet

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
TV_MAC = _config["tv_mac"]
SOURCES = _config["sources"]  # e.g. {"appletv": 1, "ps5": 3}
INPUTS_TO_SOURCES = {v: k for k, v in SOURCES.items()}
TV_TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tv-token.txt")

cec.init()
cec_lock = threading.Lock()
active_source = None
active_source_lock = threading.Lock()

# ---------------------------------------------------------------------------
# TV control
# ---------------------------------------------------------------------------


def tv_power_state() -> str:
    """Return 'on', 'standby', or 'unreachable'."""
    try:
        r = requests.get(f"http://{TV_IP}:8001/api/v2/", timeout=TV_API_TIMEOUT)
        if r.json()["device"]["PowerState"] == "on":
            return "on"
        return "standby"
    except (requests.RequestException, KeyError):
        return "unreachable"


def cec_set_active_source(hdmi_input: int):
    with cec_lock:
        cec.transmit(
            cec.CECDEVICE_BROADCAST,
            CEC_OPCODE_ACTIVE_SOURCE,
            bytes([hdmi_input << 4, 0x00]),
        )


def tv_on(source: str) -> tuple[bool, str]:
    global active_source
    hdmi_input = SOURCES[source]
    try:
        state = tv_power_state()
        if state == "on":
            log.debug("tv_on: already on, CEC HDMI %d for %s", hdmi_input, source)
        else:
            deadline = time.monotonic() + POWER_POLL_TIMEOUT
            if state == "unreachable":
                log.debug("tv_on: WoL to %s for %s", TV_MAC, source)
                send_magic_packet(TV_MAC)
                while tv_power_state() == "unreachable":
                    if time.monotonic() > deadline:
                        return False, "Timed out waiting for TV to become reachable"
                    time.sleep(POWER_POLL_INTERVAL)
            state = tv_power_state()
            if state == "standby":
                log.debug("tv_on: KEY_POWER then CEC HDMI %d for %s", hdmi_input, source)
                tv = SamsungTVWS(host=TV_IP, port=8002, token_file=TV_TOKEN_FILE, name="TVMaster")
                tv.send_key("KEY_POWER")
                tv.close()
                while tv_power_state() != "on":
                    if time.monotonic() > deadline:
                        return False, "Timed out waiting for TV to turn on"
                    time.sleep(POWER_POLL_INTERVAL)
        cec_set_active_source(hdmi_input)
        with active_source_lock:
            active_source = source
        return True, f"TV on, source {source} (HDMI {hdmi_input})"
    except Exception as e:
        log.error("tv_on failed: %s", e)
        return False, str(e)


def tv_off(source: str) -> tuple[bool, str]:
    global active_source
    try:
        if tv_power_state() != "on":
            log.debug("tv_off: already off")
            return True, "TV already off"
        with active_source_lock:
            if active_source is not None and active_source != source:
                log.debug("tv_off: ignoring, active source is %s not %s", active_source, source)
                return True, f"TV in use by {active_source}"
        log.debug("tv_off: KEY_POWER for %s", source)
        tv = SamsungTVWS(host=TV_IP, port=8002, token_file=TV_TOKEN_FILE, name="TVMaster")
        tv.send_key("KEY_POWER")
        tv.close()
        with active_source_lock:
            active_source = None
        return True, "TV turned off"
    except Exception as e:
        log.error("tv_off failed: %s", e)
        return False, str(e)


def tv_status() -> tuple[bool, str]:
    try:
        log.debug("tv_status")
        return True, "on" if tv_power_state() == "on" else "off"
    except Exception as e:
        log.error("tv_status failed: %s", e)
        return False, str(e)


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask("tvmaster")


def resolve_source() -> tuple[str, None] | tuple[None, str]:
    """Return (source_name, None) or (None, error_message) from request JSON."""
    if not request.is_json:
        return None, "Request must be JSON"
    body = request.json
    if "source" in body:
        source = body["source"]
        if source not in SOURCES:
            return None, f"Unknown source '{source}', expected one of: {', '.join(SOURCES)}"
        return source, None
    if "input" in body:
        hdmi_input = int(body["input"])
        if hdmi_input not in INPUTS_TO_SOURCES:
            valid = ', '.join(str(i) for i in sorted(INPUTS_TO_SOURCES))
            return None, f"Unknown input {hdmi_input}, expected one of: {valid}"
        return INPUTS_TO_SOURCES[hdmi_input], None
    return None, "Missing required 'source' or 'input' field"


@app.route("/tv/status", methods=["GET"])
def tv_status_handler():
    ok, message = tv_status()
    status = 200 if ok else 500
    return jsonify(ok=ok, message=message), status


@app.route("/tv/on", methods=["POST"])
def tv_on_handler():
    source, err = resolve_source()
    if err:
        return jsonify(ok=False, message=err), 400
    ok, message = tv_on(source)
    status = 200 if ok else 500
    return jsonify(ok=ok, message=message), status


@app.route("/tv/off", methods=["POST"])
def tv_off_handler():
    source, err = resolve_source()
    if err:
        return jsonify(ok=False, message=err), 400
    ok, message = tv_off(source)
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
