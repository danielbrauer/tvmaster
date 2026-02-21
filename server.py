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

import cec
from flask import Flask, jsonify, request

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

LAN_HOST = "0.0.0.0"
LAN_PORT = 8080

CEC_OPCODE_ACTIVE_SOURCE = 0x82
CEC_OPCODE_GIVE_DEVICE_POWER_STATUS = 0x8F
CEC_OPCODE_REPORT_POWER_STATUS = 0x90

POWER_STATUS_ON = 0x00
POWER_STATUS_STANDBY = 0x01
POWER_STATUS_TRANSITION_TO_ON = 0x02
POWER_STATUS_TRANSITION_TO_STANDBY = 0x03

POWER_QUERY_INTERVAL = 1.0    # seconds between status queries
POWER_ON_TIMEOUT = 20.0       # total timeout for power-on
POWER_OFF_TIMEOUT = 8.0       # total timeout for power-off

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


class _PowerStatusState:
    """Thread-safe container for the latest Report Power Status byte."""

    def __init__(self):
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._status: int | None = None

    def clear(self):
        with self._lock:
            self._status = None
            self._event.clear()

    def set(self, status: int):
        with self._lock:
            self._status = status
            self._event.set()

    def wait(self, timeout: float) -> int | None:
        """Wait up to *timeout* seconds for a status byte. Returns the byte or None."""
        if self._event.wait(timeout):
            with self._lock:
                return self._status
        return None


_power_status = _PowerStatusState()


def _cec_command_callback(event, command):
    """CEC command callback — filters for Report Power Status from the TV."""
    if (
        command["opcode"] == CEC_OPCODE_REPORT_POWER_STATUS
        and command["initiator"] == cec.CECDEVICE_TV
    ):
        status = command["parameters"][0] if command["parameters"] else None
        if status is not None:
            log.debug("CEC callback: Report Power Status = 0x%02X", status)
            _power_status.set(status)

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
    cec.add_callback(_cec_command_callback, cec.EVENT_COMMAND)

    _tv = cec.Device(cec.CECDEVICE_TV)
    _cec_ready = True
    log.info("CEC initialized, TV device ready")


# ---------------------------------------------------------------------------
# CEC helpers
# ---------------------------------------------------------------------------


def _wait_for_power_status(target: set[int], timeout: float) -> bool:
    """Poll TV power status via CEC callback until it matches *target*.

    Must be called while holding _cec_lock.

    Sends ``Give Device Power Status`` (0x8F) every POWER_QUERY_INTERVAL and
    listens for ``Report Power Status`` (0x90) via the async callback.
    """
    deadline = time.monotonic() + timeout
    cycle = 0
    while time.monotonic() < deadline:
        cycle += 1
        _power_status.clear()
        cec.transmit(cec.CECDEVICE_TV, CEC_OPCODE_GIVE_DEVICE_POWER_STATUS, bytes())
        log.debug("_wait_for_power_status: sent Give Device Power Status (cycle %d)", cycle)

        remaining = deadline - time.monotonic()
        wait_time = min(POWER_QUERY_INTERVAL, max(remaining, 0))
        status = _power_status.wait(wait_time)

        if status is None:
            log.debug("_wait_for_power_status: no response (cycle %d)", cycle)
            continue

        if status in target:
            log.debug("_wait_for_power_status: target reached (0x%02X, cycle %d)", status, cycle)
            return True

        if status in (POWER_STATUS_TRANSITION_TO_ON, POWER_STATUS_TRANSITION_TO_STANDBY):
            log.debug("_wait_for_power_status: TV in transition (0x%02X, cycle %d)", status, cycle)
            continue

        log.debug("_wait_for_power_status: status 0x%02X not in target (cycle %d)", status, cycle)

    log.warning("_wait_for_power_status: timed out after %.1fs", timeout)
    return False


def tv_on(hdmi_input: int | None = None) -> tuple[bool, str]:
    if not _cec_ready:
        return False, "CEC not initialized"
    with _cec_lock:
        try:
            log.debug("tv_on: power_on" + (f" + HDMI {hdmi_input}" if hdmi_input else ""))
            _tv.power_on()

            if not _wait_for_power_status({POWER_STATUS_ON}, POWER_ON_TIMEOUT):
                return False, "TV did not turn on"

            # Switch input after TV is confirmed on
            if hdmi_input is not None:
                cec.transmit(
                    cec.CECDEVICE_BROADCAST,
                    CEC_OPCODE_ACTIVE_SOURCE,
                    bytes([hdmi_input << 4, 0x00]),
                )
                log.debug("tv_on: sent Active Source for HDMI %d", hdmi_input)

            return True, "TV turned on"
        except Exception as e:
            log.error("tv_on failed: %s", e)
            return False, str(e)


def tv_off() -> tuple[bool, str]:
    if not _cec_ready:
        return False, "CEC not initialized"
    with _cec_lock:
        try:
            log.debug("tv_off: standby")
            _tv.standby()

            if not _wait_for_power_status({POWER_STATUS_STANDBY}, POWER_OFF_TIMEOUT):
                return False, "TV did not turn off"

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
