# API Key Management Guide

The BEACON Dashboard relies on two primary APIs to function:
1. **OpenWeatherMap API** (For raw weather data)
2. **Google Gemini API** (For Buddy's AI dialogue and processing)

## Obtaining the Keys

- **Google Gemini API:** Navigate to Google AI Studio and create a free API key.
- **OpenWeatherMap API:** Navigate to OpenWeatherMap to sign up and get a free API key.

## How to Swap Keys

Your keys are managed entirely through environment variables. They should **never** be hardcoded into the source files.

### 1. For Local Testing
Edit the `.env` file located in the root directory of the project:
```env
GEMINI_API_KEY="your_new_gemini_key"
OPENWEATHERMAP_API_KEY="your_new_openweathermap_key"
ADMIN_PASSWORD="your_secure_admin_password"
```
Then, restart the Python application or Docker container.

### 2. For Production (GitHub Actions Deployment)
Since the project uses a self-hosted GitHub Runner for deployment:
1. Go to your GitHub repository -> **Settings** -> **Secrets and variables** -> **Actions**.
2. Update the `GEMINI_API_KEY` and `OPENWEATHERMAP_API_KEY` repository secrets.
3. Trigger a new deployment by pushing to the `main` branch or running the deployment workflow manually. The CI/CD pipeline will automatically inject the new keys securely.