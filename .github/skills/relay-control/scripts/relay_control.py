#!/usr/bin/env python3
"""
Relay Control Script
Reads automation_config.ini from a device via ADB, resolves the
Raspberry Pi IP from MongoDB, and toggles the ignition relay.
"""

import argparse
import configparser
import io
import subprocess
import sys

def run_adb(serial, command):
    """Run an ADB shell command and return stdout."""
    cmd = ["adb", "-s", serial, "shell", command]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        print(f"ADB command failed: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def read_automation_config(serial):
    """Read and parse automation_config.ini from the device."""
    raw = run_adb(serial, "cat /home/ubuntu/config/automation_config.ini")
    if not raw or "No such file" in raw:
        print("ERROR: automation_config.ini not found on device", file=sys.stderr)
        sys.exit(1)

    config = configparser.ConfigParser()
    config.read_string(raw)

    section = "test_automation"
    if not config.has_section(section):
        print(f"ERROR: [{section}] section not found in config", file=sys.stderr)
        sys.exit(1)

    automation = config.get(section, "automation", fallback="false")
    if automation.lower() != "true":
        print("ERROR: automation is disabled on this device", file=sys.stderr)
        sys.exit(1)

    raspberry_id = config.get(section, "raspberry_id", fallback=None)
    relay_no = config.get(section, "ignition_relay_no", fallback=None)
    relay_id = config.get(section, "ignition_relay_id", fallback=None)

    if not raspberry_id:
        print("ERROR: raspberry_id not found in config", file=sys.stderr)
        sys.exit(1)
    if relay_no is None:
        print("ERROR: ignition_relay_no not found in config", file=sys.stderr)
        sys.exit(1)

    return raspberry_id, relay_no, relay_id


def get_pi_ip(raspberry_id):
    """Look up the Raspberry Pi IP from MongoDB."""
    try:
        from pymongo import MongoClient
    except ImportError:
        print("ERROR: pymongo not installed. Run: pip install pymongo", file=sys.stderr)
        sys.exit(1)

    client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=5000)
    doc = client["ip_db"]["automation_peripheral"].find_one({"Username": raspberry_id})
    if not doc:
        print(f"ERROR: No MongoDB document found for Username={raspberry_id}", file=sys.stderr)
        sys.exit(1)

    ip_address = doc.get("IP_Address")
    if not ip_address:
        print(f"ERROR: IP_Address field missing in document for {raspberry_id}", file=sys.stderr)
        sys.exit(1)

    return ip_address


def send_relay_command(pi_ip, relay_id, relay_no, action):
    """Send HTTP command to the relay server on the Pi."""
    port = 8081
    if relay_id:
        url = f"http://{pi_ip}:{port}/Relay?id={relay_id}&{relay_no}={action}"
    else:
        url = f"http://{pi_ip}:{port}/Relay?{relay_no}={action}"

    print(f"Sending: curl \"{url}\"")
    result = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", url],
        capture_output=True, text=True, timeout=15
    )

    http_code = result.stdout.strip()
    if result.returncode != 0:
        print(f"FAILED: curl error — {result.stderr.strip()}", file=sys.stderr)
        return False

    if http_code.startswith("2"):
        print(f"SUCCESS: HTTP {http_code}")
        return True
    else:
        print(f"FAILED: HTTP {http_code}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Control ignition relay via Raspberry Pi")
    parser.add_argument("--serial", required=True, help="ADB device serial")
    parser.add_argument("--action", required=True, choices=["on", "off"], help="Relay action")
    args = parser.parse_args()

    print(f"=== Relay Control: {args.action.upper()} ===")
    print(f"Device: {args.serial}")

    # Step 1: Read config from device
    print("\n[1/3] Reading automation_config.ini from device...")
    raspberry_id, relay_no, relay_id = read_automation_config(args.serial)
    print(f"  raspberry_id:       {raspberry_id}")
    print(f"  ignition_relay_no:  {relay_no}")
    print(f"  ignition_relay_id:  {relay_id or '(not set)'}")

    # Step 2: Look up Pi IP
    print(f"\n[2/3] Looking up IP for {raspberry_id} in MongoDB...")
    pi_ip = get_pi_ip(raspberry_id)
    print(f"  Pi IP: {pi_ip}")

    # Step 3: Send relay command
    print(f"\n[3/3] Sending relay command ({args.action})...")
    success = send_relay_command(pi_ip, relay_id, relay_no, args.action)

    # Summary
    print(f"\n{'='*40}")
    print(f"Relay Control Result:")
    print(f"  Device:       {args.serial}")
    print(f"  Raspberry Pi: {raspberry_id} ({pi_ip})")
    print(f"  Relay ID:     {relay_id or 'N/A'}")
    print(f"  Channel:      {relay_no}")
    print(f"  Action:       {args.action}")
    print(f"  Result:       {'SUCCESS' if success else 'FAILED'}")

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
