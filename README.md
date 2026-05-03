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

4. **Run via Docker (Recommended):**
   ```bash
   sudo docker-compose up -d --build
   ```
   Or run it locally with Python:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   python app.py
   ```

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
