#!/usr/bin/env python3
"""Send WoL to TV and measure how long until it responds to ping."""

import json
import os
import subprocess
import time

from wakeonlan import send_magic_packet

config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
with open(config_path) as f:
    config = json.load(f)

TV_IP = config["tv_ip"]
TV_MAC = config["tv_mac"]

print(f"Sending WoL to {TV_MAC} via {TV_IP}...")
t0 = time.monotonic()
send_magic_packet(TV_MAC, ip_address=TV_IP)

print(f"Pinging {TV_IP} every second...")
while True:
    elapsed = time.monotonic() - t0
    result = subprocess.run(
        ["ping", "-c", "1", "-W", "1", TV_IP],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode == 0:
        print(f"  {elapsed:5.1f}s - RESPONSIVE")
        break
    else:
        print(f"  {elapsed:5.1f}s - no response")
    time.sleep(1)

print(f"\nTV responded to ping {elapsed:.1f}s after WoL")
