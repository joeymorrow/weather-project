#!/bin/bash
# BEACON D-Bus Service Installer

echo "================================================="
echo "  BEACON D-Bus Listener Service Setup"
echo "================================================="

set -e # Exit immediately if any command fails

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

# List packages to install with pip
PIP_PACKAGES="pydbus requests python-dotenv"

# Attempt pip install, with fallback to --break-system-packages
echo "Attempting to install Python packages with 'python3 -m pip'..."
if ! python3 -m pip install $PIP_PACKAGES; then
    echo "Pip installation failed without --break-system-packages. Retrying with it..."
    if ! python3 -m pip install $PIP_PACKAGES --break-system-packages; then
        echo "FATAL: Failed to install Python dependencies via pip even with --break-system-packages."
        echo "This indicates a deeper Python environment issue."
        echo "Please try manually installing: 'sudo python3 -m pip install $PIP_PACKAGES --break-system-packages'"
        echo "Or check your Python path: 'sudo python3 -c \"import site; print(site.getsitepackages())\"'"
        exit 1 # Exit due to fatal error
    fi
fi

# Post-install verification: Check if pydbus is actually importable
echo "Verifying 'pydbus' can be imported by '/usr/bin/python3'..."
if ! /usr/bin/python3 -c "import pydbus" &> /dev/null; then
    echo "FATAL: 'pydbus' was installed, but '/usr/bin/python3' cannot import it."
    echo "This often happens if packages are installed for a different Python environment or user."
    echo "Please try explicitly installing pydbus for the system Python with:"
    echo "  sudo apt install python3-pydbus"
    echo "OR (if apt doesn't have it):"
    echo "  sudo /usr/bin/python3 -m pip install pydbus --break-system-packages"
    echo "Then re-run this install script."
    exit 1 # Exit due to fatal error
    fi
fi
echo "Python dependencies installed."

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

# Ensure .env file is readable by root, as the service runs as root
if [ -f "$PROJECT_DIR/.env" ]; then
    echo "Setting ownership of .env to root for service access..."
    chown root:root "$PROJECT_DIR/.env"
    chmod 600 "$PROJECT_DIR/.env" # Ensure only root can read it
else
    echo "⚠️ .env file not found. D-Bus service might fail to load INTERNAL_API_SECRET."
    echo "Please ensure .env is in the project root with INTERNAL_API_SECRET defined."
fi

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
