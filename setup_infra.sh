#!/bin/bash
# BEACON Infrastructure Automation Script
# Automates prerequisites, secrets management, and Cloudflare tunneling

echo "================================================="
echo "  🚀 BEACON Infrastructure Setup Script"
echo "================================================="

echo -e "\n[1/4] Installing system prerequisites..."
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv git curl docker.io docker-compose

# Enable Docker to start on boot
sudo systemctl enable docker
sudo systemctl start docker

echo -e "\n[2/4] Configuring Environment Secrets (.env)..."
if [ ! -f .env ]; then
    read -p "Enter Gemini API Key: " GEMINI_API_KEY
    read -p "Enter OpenWeatherMap API Key: " OWM_API_KEY
    read -p "Enter Admin Password (for /admin override): " ADMIN_PASS
    
    echo "GEMINI_API_KEY=\"$GEMINI_API_KEY\"" > .env
    echo "OPENWEATHERMAP_API_KEY=\"$OWM_API_KEY\"" >> .env
    echo "ADMIN_PASSWORD=\"$ADMIN_PASS\"" >> .env
    chmod 600 .env
    echo "✅ .env file generated securely."
else
    echo "⚠️ .env already exists, skipping secret generation."
fi

echo -e "\n[3/4] Checking Cloudflare Tunnel setup..."
if ! command -v cloudflared &> /dev/null; then
    read -p "Do you want to install Cloudflared for secure external access? (y/N) " INSTALL_CF
    if [[ "$INSTALL_CF" =~ ^[Yy]$ ]]; then
        curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
        sudo dpkg -i cloudflared.deb
        rm cloudflared.deb
        echo "Cloudflared installed successfully!"
        read -p "Enter your Cloudflare Tunnel Token (leave blank to configure later): " CF_TOKEN
        if [ ! -z "$CF_TOKEN" ]; then
            sudo cloudflared service install $CF_TOKEN
        fi
    fi
else
    echo "✅ Cloudflared is already installed."
fi

echo -e "\n[4/4] GitHub Actions CI/CD Next Steps"
echo "-------------------------------------------------"
echo "To complete infrastructure setup for automated deployments:"
echo "1. Go to your GitHub Repo -> Settings -> Actions -> Runners"
echo "2. Click 'Add self-hosted runner' and follow GitHub's instructions."
echo "3. Run 'sudo ./svc.sh install' and 'sudo ./svc.sh start' within the runner folder to persist it."
echo ""
echo "🔑 Ensure the following are added to GitHub Repository Secrets:"
echo "  - GEMINI_API_KEY"
echo "  - OPENWEATHERMAP_API_KEY"
echo "  - ADMIN_PASSWORD"
echo "-------------------------------------------------"

echo -e "\n🎉 Setup Complete! To test locally, you can run:"
echo "docker compose up --build"
echo "================================================="