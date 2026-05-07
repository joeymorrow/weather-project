#!/bin/bash

echo "=== 🐧 UBUNTU GPU & BROWSER DIAGNOSTIC 🐧 ==="
echo "1. Checking Installed GPU Hardware:"
lspci -nn | grep -iE 'vga|3d|display'
echo "------------------------------------------------"

echo "2. Checking Active Graphics Drivers:"
lshw -c video 2>/dev/null | grep -iE 'vendor|product|configuration' || echo "(Run script with 'sudo' for deeper driver info)"
echo "------------------------------------------------"

echo "3. Checking Display Server:"
echo "Current Session Type: ${XDG_SESSION_TYPE:-Unknown}"
echo "------------------------------------------------"

echo "4. Checking for essential Linux GPU libraries:"
dpkg -l | grep -iE 'libvulkan1|mesa-vulkan-drivers|libgl1-mesa-dri|nvidia-driver' | awk '{print $2, $3}' || echo "No essential libraries found."
echo "================================================"

echo "🛠️ HOW TO FORCE-ENABLE YOUR GPU ON UBUNTU BROWSER:"
echo "If you are using Chrome / Chromium / Edge / Brave:"
echo "  1. Navigate to: chrome://flags"
echo "  2. Search for 'Override software rendering list' and set it to ENABLED."
echo "  3. Search for 'Preferred Ozone platform' and set it to AUTO (or Wayland if session is Wayland)."
echo "  4. Relaunch the browser."
echo ""
echo "If you are using Firefox:"
echo "  1. Navigate to: about:config"
echo "  2. Search for 'layers.acceleration.force-enabled' and set to TRUE."
echo "  3. Search for 'gfx.webrender.all' and set to TRUE."
echo "  4. Restart Firefox."