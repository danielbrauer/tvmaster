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
import threading

import cec
from flask import Flask, jsonify, request

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

LAN_HOST = "0.0.0.0"
LAN_PORT = 8080

CEC_OPCODE_USER_CONTROL_PRESSED = 0x44
CEC_OPCODE_USER_CONTROL_RELEASED = 0x45
CEC_OPCODE_ACTIVE_SOURCE = 0x82
CEC_UI_COMMAND_POWER_OFF_FUNCTION = 0x6C

# Set log level via LOG_LEVEL env var (e.g. LOG_LEVEL=DEBUG)
log_level = os.environ.get("LOG_LEVEL", "WARNING").upper()
logging.basicConfig(level=getattr(logging, log_level, logging.WARNING))
log = logging.getLogger("tvmaster")

# ---------------------------------------------------------------------------
# CEC state
# ---------------------------------------------------------------------------

_cec_lock = threading.Lock()
_cec_ready = False
_tv = None


# ---------------------------------------------------------------------------
# CEC init
# ---------------------------------------------------------------------------


def init_cec():
    """Initialize the CEC adapter (once at startup)."""
    global _cec_ready, _tv

    adapters = cec.list_adapters()
    if not adapters:
        log.error("No CEC adapters found")
        return

    log.info("CEC adapters: %s", adapters)
    cec.init(adapters[0])

    _tv = cec.Device(cec.CECDEVICE_TV)
    _cec_ready = True
    log.info("CEC initialized, TV device ready")


def tv_on(hdmi_input: int | None = None) -> tuple[bool, str]:
    if not _cec_ready:
        return False, "CEC not initialized"
    with _cec_lock:
        try:
            log.debug("tv_on: power_on" + (f" + HDMI {hdmi_input}" if hdmi_input else ""))
            _tv.power_on()
            if hdmi_input is not None:
                cec.transmit(
                    cec.CECDEVICE_BROADCAST,
                    CEC_OPCODE_ACTIVE_SOURCE,
                    bytes([hdmi_input << 4, 0x00]),
                )
            return True, "TV turned on"
        except Exception as e:
            log.error("tv_on failed: %s", e)
            return False, str(e)


def tv_off() -> tuple[bool, str]:
    if not _cec_ready:
        return False, "CEC not initialized"
    with _cec_lock:
        try:
            log.debug("tv_off: User Control Pressed - Power Off Function")
            cec.transmit(
                cec.CECDEVICE_TV,
                CEC_OPCODE_USER_CONTROL_PRESSED,
                bytes([CEC_UI_COMMAND_POWER_OFF_FUNCTION]),
            )
            cec.transmit(
                cec.CECDEVICE_TV,
                CEC_OPCODE_USER_CONTROL_RELEASED,
                bytes(),
            )
            return True, "TV turned off"
        except Exception as e:
            log.error("tv_off failed: %s", e)
            return False, str(e)


def tv_status() -> tuple[bool, str]:
    """Query TV power status."""
    if not _cec_ready:
        return False, "CEC not initialized"
    with _cec_lock:
        try:
            log.debug("tv_status")
            return True, "on" if _tv.is_on() else "off"
        except Exception as e:
            log.error("tv_status failed: %s", e)
            return False, str(e)


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
    if hdmi_input is not None:
        hdmi_input = int(hdmi_input)
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

    init_cec()

    print(f"LAN app: {args.lan_host}:{args.lan_port}")

    try:
        app.run(host=args.lan_host, port=args.lan_port, use_reloader=False)
    except KeyboardInterrupt:
        print("\nShutting down")


if __name__ == "__main__":
    main()
