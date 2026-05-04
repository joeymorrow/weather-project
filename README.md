# Weather Project

A local weather and scheduling dashboard powered by AI.

## Setting Up on a Fresh Ubuntu Linux Environment

1. **Update and install prerequisites:**
   ```bash
   sudo apt update && sudo apt upgrade -y
   sudo apt install -y python3 python3-pip python3-venv git curl docker.io docker-compose
   ```
2. **Clone the repository:**
   ```bash
   git clone https://github.com/joeymorrow/weather-project.git
   cd weather-project
   ```
3. **Configure Environment Variables:**
   Copy the example environment file and add your keys:
   ```bash
   cp .env.example .env
   nano .env
   ```
   Add your `GEMINI_API_KEY` and `OPENWEATHERMAP_API_KEY`.

4. **Run via Python (Local Development ONLY):**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   python app.py
   ```

5. **Enable Services on Boot (Recommended):**
   To ensure your weather dashboard and deployment pipeline survive a power outage or reboot, enable the necessary services:
   ```bash
   sudo systemctl enable docker
   # If using cloudflared: sudo systemctl enable cloudflared
   # For GitHub Runner, run `sudo ./svc.sh install` and `sudo ./svc.sh start` inside your runner directory.
   ```

**🛑 IMPORTANT DEPLOYMENT RULE 🛑**
DO NOT start the Docker container manually from the host machine using `docker-compose up`. 
Deployment is STRICTLY managed by the self-hosted GitHub Runner. 
To deploy, commit your changes and push to the `ci\cd` branch. 
The runner will securely construct the `.env` file automatically using GitHub Secrets and restart the container.

## Contributing and Setting Up VS Code

If you are new to contributing to a GitHub repository, follow these steps:

### 1. Download and Install VS Code
- **Windows:** Go to [code.visualstudio.com](https://code.visualstudio.com/) and download the Windows installer. Run the installer and follow the prompts.
- **Linux (Ubuntu):** You can install it via Snap:
  ```bash
  sudo snap install --classic code
  ```

### 2. Connect Your GitHub Account to VS Code
1. Open VS Code.
2. Click on the **Accounts** icon (a person profile icon) at the bottom-left of the activity bar.
3. Select **Turn on Settings Sync** or simply click **Sign in with GitHub**.
4. A browser window will open. Authorize VS Code to access your GitHub account.
5. Once authorized, you can clone, commit, and push directly from the Source Control view in VS Code.

### 3. Setting Up Your API Tokens

You will need API keys to run the AI features and fetch live weather data.

1. **Gemini API Key:** Go to [Google AI Studio](https://aistudio.google.com/) to create a free API key.
2. **OpenWeatherMap API Key:** Go to [OpenWeatherMap](https://openweathermap.org/api) to sign up and get a free weather API key.
3. Create a `.env` file in the root of the project (if you have not already) and add your tokens:
   ```env
   GEMINI_API_KEY="your_actual_gemini_key_here"
   OPENWEATHERMAP_API_KEY="your_actual_open_weather_map_key"
   ```
   *Note: `.env` is ignored by Git, ensuring your secrets are never pushed to the repository.*

### 4. Setting up Public Access (Cloudflare Tunnel)

To expose this local dashboard to the internet securely without opening firewall ports, we recommend using Cloudflare Tunnels:

1. **Create a Cloudflare Account:** Go to [dash.cloudflare.com](https://dash.cloudflare.com) and sign up.
2. **Access Zero Trust:** Navigate to "Zero Trust" -> "Networks" -> "Tunnels".
3. **Create a Tunnel:** Click "Create a tunnel", choose "Cloudflared", and give it a name (e.g., "weather-dashboard").
4. **Install Cloudflared on your server:** Choose the Linux/Debian environment on the Cloudflare dashboard. It will provide a command similar to:
   ```bash
   curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
   sudo dpkg -i cloudflared.deb
   sudo cloudflared service install <YOUR_UNIQUE_TUNNEL_TOKEN>
   ```
5. **Route the Traffic:** In the Cloudflare Tunnel setup, map a Public Hostname (e.g., `weather.yourdomain.com`) to the local service URL: `http://localhost:5000`.

*Note: The tunnel token (`<YOUR_UNIQUE_TUNNEL_TOKEN>`) is highly sensitive. It is managed directly by the `cloudflared` system service and should never be added to your `.env` file or GitHub repository.*

## TV Display via ADB & Cron

The project includes a `tv_schedule.sh` script to automatically turn a TV or display on/off and open the weather dashboard using ADB (Android Debug Bridge). 

1. Ensure the `adb` package is installed:
   ```bash
   sudo apt install adb
   ```
2. Enable **Developer Options** and **Network Debugging** on your Android TV/device.
3. Edit your `.env` file to include your TV's local IP address and your dashboard's URL:
   ```env
   TV_IP="192.168.1.X"
   DASHBOARD_URL="https://your-dashboard-url.com"
   ```
4. Test the script manually to confirm it can wake the device and launch the URL:
   ```bash
   ./tv_schedule.sh on
   ```
   *(Note: The first time you connect, your TV will display a prompt asking you to allow the connection. You must select "Always allow from this computer".)*

### Automating with Cron

To automate turning the TV on and off at specific times (e.g., turning on at 7:00 AM and off at 10:00 PM), add the script to your cron jobs:

1. Open your crontab:
   ```bash
   crontab -e
   ```
2. Add the following lines (adjust the paths and times as needed):
   ```cron
   # Turn TV on at 7:00 AM every day
   0 7 * * * /path/to/weather-project/tv_schedule.sh on

   # Turn TV off at 10:00 PM every day
   0 22 * * * /path/to/weather-project/tv_schedule.sh off
   ```
