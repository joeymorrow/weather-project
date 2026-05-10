#!/bin/bash
# BEACON D-Bus Service Installer

echo "================================================="
echo "  BEACON D-Bus Listener Service Setup"
echo "================================================="

# Check for root privileges
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (sudo ./install_dbus_service.sh)"
  exit 1
fi

# Find the project directory, assuming the script is in the project root
PROJECT_DIR=$(dirname "$(realpath "$0")")
SERVICE_NAME="beacon-dbus.service"
SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME"
DBUS_CONF_FILE="/etc/dbus-1/system.d/com.morrowedge.Beacon.conf"

echo "[1/4] Installing D-Bus and Python dependencies..."
apt-get update
apt-get install -y python3-gi python3-dbus gir1.2-glib-2.0
# Assuming pip is for python3
pip install pydbus requests python-dotenv

echo "[2/4] Creating D-Bus policy file at $DBUS_CONF_FILE..."
cat > "$DBUS_CONF_FILE" << EOL
<!DOCTYPE busconfig PUBLIC
 "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <policy user="root">
    <allow own="com.morrowedge.Beacon"/>
  </policy>
  <policy context="default">
    <allow send_destination="com.morrowedge.Beacon"/>
  </policy>
</busconfig>
EOL

echo "[3/4] Creating systemd service file at $SERVICE_FILE..."
cat > "$SERVICE_FILE" << EOL
[Unit]
Description=BEACON DBus System Listener
Wants=dbus.socket
After=dbus.socket network.target

[Service]
Type=simple
User=root
WorkingDirectory=$PROJECT_DIR
ExecStart=/usr/bin/python3 $PROJECT_DIR/dbus_listener.py
Restart=on-failure
# Load environment variables from the project's .env file
EnvironmentFile=$PROJECT_DIR/.env

[Install]
WantedBy=multi-user.target
EOL

echo "[4/4] Enabling and starting the D-Bus listener service..."
systemctl daemon-reload
systemctl enable $SERVICE_NAME
systemctl restart $SERVICE_NAME

echo "Setup complete!"
echo "The D-Bus service is now running on the System Bus."
echo "You can check its status with: systemctl status $SERVICE_NAME"
echo "You can send commands using gdbus:"
echo "Example: gdbus call --system --dest com.morrowedge.Beacon --object-path /com/morrowedge/Beacon --method com.morrowedge.Beacon.Control.TriggerSync"
echo "================================================="