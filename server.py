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
import time
from collections.abc import Callable

import cec
from flask import Flask, jsonify, request

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

LAN_HOST = "0.0.0.0"
LAN_PORT = 8080

CEC_OPCODE_ACTIVE_SOURCE = 0x82
MAX_RETRIES = 3
RETRY_DELAY = 1.0  # seconds between status checks
POWER_ON_DELAY = 4.0  # seconds to wait after power-on commands

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


# ---------------------------------------------------------------------------
# CEC helpers
# ---------------------------------------------------------------------------


def _cec_retry(name: str, command: Callable, check: Callable[[], bool], delay: float = RETRY_DELAY) -> bool:
    """Send a CEC command and retry until check() returns True.

    Must be called while holding _cec_lock.
    """
    for attempt in range(MAX_RETRIES):
        log.debug("%s: attempt %d — sending command", name, attempt + 1)
        command()
        log.debug("%s: attempt %d — waiting %.1fs", name, attempt + 1, delay)
        time.sleep(delay)
        ok = check()
        log.debug("%s: attempt %d — check=%s", name, attempt + 1, ok)
        if ok:
            return True
        log.warning("%s: attempt %d — check failed, retrying", name, attempt + 1)
    log.error("%s: failed after %d attempts", name, MAX_RETRIES)
    return False


def tv_on(hdmi_input: int | None = None) -> tuple[bool, str]:
    if not _cec_ready:
        return False, "CEC not initialized"
    with _cec_lock:
        try:
            transmit = None
            if hdmi_input is not None:
                transmit = lambda: cec.transmit(
                    cec.CECDEVICE_BROADCAST,
                    CEC_OPCODE_ACTIVE_SOURCE,
                    bytes([hdmi_input << 4, 0x00]),
                )
            # Optimistic: fire all commands in quick succession
            log.debug("tv_on: optimistic power_on" + (f" + switch to HDMI {hdmi_input}" if transmit else ""))
            _tv.power_on()
            if transmit:
                transmit()
            time.sleep(POWER_ON_DELAY)
            # Check final state, work backwards from there
            if transmit and _is_active_source() and _tv.is_on():
                log.debug("tv_on: success on first try")
                return True, "TV turned on"
            if not transmit and _tv.is_on():
                log.debug("tv_on: success on first try")
                return True, "TV turned on"
            # Troubleshoot: which stage failed?
            log.warning("tv_on: not fully successful after optimistic attempt")
            if not _tv.is_on():
                log.warning("tv_on: TV not on, retrying power_on")
                if not _cec_retry("power_on", _tv.power_on, _tv.is_on, POWER_ON_DELAY):
                    return False, "TV did not turn on after retries"
            if transmit and not _is_active_source():
                log.warning("tv_on: not active source, retrying switch_input")
                if not _cec_retry("switch_input", transmit, _is_active_source):
                    return False, "Input switch failed after retries"
            return True, "TV turned on"
        except Exception as e:
            log.error("tv_on failed: %s", e)
            return False, str(e)


def _is_active_source() -> bool:
    return cec.is_active_source(cec.CECDEVICE_RECORDINGDEVICE1)


def tv_off() -> tuple[bool, str]:
    if not _cec_ready:
        return False, "CEC not initialized"
    with _cec_lock:
        try:
            # Optimistic: fire both commands in quick succession
            log.debug("tv_off: optimistic set_active_source + standby")
            cec.set_active_source()
            _tv.standby()
            time.sleep(RETRY_DELAY)
            if not _tv.is_on():
                log.debug("tv_off: success on first try")
                return True, "TV turned off"
            # Troubleshoot: which stage failed?
            log.warning("tv_off: TV still on after optimistic attempt")
            if not _is_active_source():
                log.warning("tv_off: not active source, retrying set_active_source")
                if not _cec_retry("set_active_source", cec.set_active_source, _is_active_source):
                    return False, "set_active_source failed after retries"
            if not _cec_retry("standby", _tv.standby, lambda: not _tv.is_on()):
                return False, "TV did not turn off after retries"
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
