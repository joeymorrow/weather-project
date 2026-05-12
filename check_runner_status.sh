#!/bin/bash

echo "================================================="
echo " 🔍 BEACON CI/CD: Runner Status Diagnostics"
echo "================================================="

# 1. Check if the runner process is running
RUNNER_PID=$(pgrep -f Runner.Listener)
if [ -z "$RUNNER_PID" ]; then
    echo "❌ ERROR: No active GitHub Actions Runner process found in memory."
else
    echo "✅ Runner process is actively running (PID: $RUNNER_PID)."
fi

# 2. Find and check the systemd service status
echo -e "\n📋 Checking systemd service registry:"
SERVICE_NAME=$(systemctl list-units --type=service | grep -i "actions.runner" | awk '{print $1}')

if [ -z "$SERVICE_NAME" ]; then
    echo "⚠️ No 'actions.runner' service found in systemd."
    echo "   Ensure you navigated to your runner directory and ran:"
    echo "   sudo ./svc.sh install && sudo ./svc.sh start"
else
    echo "✅ Found systemd service: $SERVICE_NAME"
    systemctl status "$SERVICE_NAME" --no-pager | head -n 10
    echo -e "\n📝 Recent Error Logs (Last 15 lines):"
    journalctl -u "$SERVICE_NAME" -n 15 --no-pager --output=cat
fi