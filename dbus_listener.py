#!/usr/bin/env python3
import os
import requests
import subprocess
from dotenv import load_dotenv
from pydbus import SystemBus
from gi.repository import GLib

# --- Configuration ---
# This script assumes it's run from the project's root directory or as a systemd service
# with the WorkingDirectory and EnvironmentFile set correctly.
load_dotenv()
INTERNAL_API_SECRET = os.environ.get("INTERNAL_API_SECRET")
APP_PORT = os.environ.get("APP_PORT", 5000)
APP_URL = f"http://127.0.0.1:{APP_PORT}/api/internal/action"

# D-Bus configuration
BUS_NAME = 'com.morrowedge.Beacon'
OBJECT_PATH = '/com/morrowedge/Beacon'
INTERFACE_NAME = 'com.morrowedge.Beacon.Control'

# --- Sanitization Lists ---
VALID_STATIONS = ["office", "gym", "store", "library", "garage", "park", "kitchen", "bed", "coffee"]

class BeaconDBusService:
    """
    <node>
        <interface name='com.morrowedge.Beacon.Control'>
            <method name='TriggerSync'>
                <arg type='b' name='success' direction='out'/>
            </method>
            <method name='MoveBuddy'>
                <arg type='s' name='station' direction='in'/>
                <arg type='b' name='success' direction='out'/>
            </method>
            <method name='SetEmergency'>
                <arg type='b' name='active' direction='in'/>
                <arg type='s' name='message' direction='in'/>
                <arg type='s' name='color' direction='in'/>
                <arg type='b' name='success' direction='out'/>
            </method>
        </interface>
    </node>
    """
    def __init__(self):
        GLib.timeout_add_seconds(5, self._push_host_services_once)
        GLib.timeout_add_seconds(60, self._push_host_services)

    def _make_request(self, payload, quiet=False):
        """Internal helper to send a secure request to app.py."""
        if not INTERNAL_API_SECRET:
            print("[DBUS ERROR] INTERNAL_API_SECRET is not set. Cannot communicate with app.")
            return False
        try:
            headers = {'X-Internal-Secret': INTERNAL_API_SECRET, 'Content-Type': 'application/json'}
            response = requests.post(APP_URL, json=payload, headers=headers, timeout=5)
            if response.status_code == 200:
                if not quiet:
                    print(f"[DBUS] Successfully executed action: {payload.get('action')}")
                return True
            else:
                print(f"[DBUS ERROR] Failed to execute action '{payload.get('action')}'. Status: {response.status_code}, Response: {response.text}")
                return False
        except requests.exceptions.RequestException as e:
            print(f"[DBUS ERROR] Communication with app.py failed: {e}")
            return False

    def _push_host_services_once(self):
        self._push_host_services()
        return False

    def _push_host_services(self):
        services_to_check = ['docker', 'cloudflared', 'cron', 'systemd-journald', 'beacon-dbus']
        service_status = []
        for s in services_to_check:
            try:
                res = subprocess.run(['systemctl', 'is-active', s], capture_output=True, text=True, timeout=2)
                status_text = res.stdout.strip()
                is_active = (status_text == 'active')
                context = ""
                if not is_active:
                    status_res = subprocess.run(['systemctl', 'status', s], capture_output=True, text=True, timeout=2)
                    full_ctx = f"{status_res.stdout.strip()}\n{status_res.stderr.strip()}".strip()
                    context = full_ctx[:300] + "..." if full_ctx else "No context available."
                service_status.append({"name": s, "active": is_active, "status": status_text, "context": context})
            except Exception as e:
                service_status.append({"name": s, "active": False, "status": "error", "context": str(e)})
        
        self._make_request({"action": "update_host_services", "services": service_status}, quiet=True)
        return True

    def TriggerSync(self):
        """Triggers a full data synchronization in the main application."""
        print("[DBUS] Received request: TriggerSync")
        return self._make_request({"action": "trigger_sync"})

    def MoveBuddy(self, station):
        """Moves Buddy to a specified station, if valid."""
        print(f"[DBUS] Received request: MoveBuddy to '{station}'")
        if station in VALID_STATIONS:
            return self._make_request({"action": "move_buddy", "station": station})
        else:
            print(f"[DBUS WARN] Invalid station '{station}' requested. Ignoring.")
            return False

    def SetEmergency(self, active, message, color):
        """Sets or clears an emergency flare."""
        print(f"[DBUS] Received request: SetEmergency (Active: {active})")
        return self._make_request({
            "action": "set_emergency",
            "active": active,
            "message": message,
            "color": color
        })

def main():
    print("--- BEACON D-Bus System Listener ---")
    if not INTERNAL_API_SECRET:
        print("FATAL: INTERNAL_API_SECRET environment variable not found.")
        print("Ensure this script is run with the .env file loaded or as a systemd service with EnvironmentFile set.")
        return

    loop = GLib.MainLoop()
    bus = SystemBus()
    bus.publish(BUS_NAME, BeaconDBusService())
    print(f"Service '{BUS_NAME}' published on the System bus.\nListening for D-Bus messages...")
    loop.run()

if __name__ == '__main__':
    main()