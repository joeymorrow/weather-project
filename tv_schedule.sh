#!/bin/bash

# Load environment variables
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$DIR/.env" ]; then
    set -a
    source "$DIR/.env"
    set +a
fi

if [ -z "$TV_IP" ]; then
    echo "Error: TV_IP is not set in .env"
    exit 1
fi

if [ -z "$DASHBOARD_URL" ]; then
    # Default URL if not set
    DASHBOARD_URL="https://saultweather.morrowedge.com"
fi

# Ensure we are connected
adb connect "$TV_IP"

if [ "$1" == "on" ]; then
    # Wake up
    adb shell input keyevent KEYCODE_WAKEUP
    # Give it a second to breathe, then force the dashboard to the front
    sleep 2
    adb shell am start -a android.intent.action.VIEW -d "$DASHBOARD_URL"
elif [ "$1" == "off" ]; then
    # Instead of KEYCODE_SLEEP, we use the Power Toggle 
    # which usually triggers the "Fast Start" / Soft Sleep mode
    adb shell input keyevent 26
else
    echo "Usage: $0 {on|off}"
fi
