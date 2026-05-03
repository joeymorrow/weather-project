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
   git clone <YOUR_GITHUB_REPO_URL>
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

### 3. Setting Up Your AI API Token
You will need an AI API key (like a Gemini API key from Google AI Studio) to run the AI features.
1. Create a `.env` file in the root of the project (if you have not already).
2. Add your tokens:
   ```env
   GEMINI_API_KEY="your_actual_api_key_here"
   OPENWEATHERMAP_API_KEY="your_open_weather_map_key"
   ```
   *Note: `.env` is ignored by Git, ensuring your secrets are never pushed to the repository.*
