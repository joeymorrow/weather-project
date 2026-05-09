# BEACON: School Dashboard Setup Generator

## Instructions for the User:
Copy the entire block below and paste it into an AI coding assistant (like Gemini Code Assist or Claude). It will automatically generate a setup script and a gzip archive configuration to spin up a fully isolated, EAP-enabled School Dashboard instance.

---

### PROMPT TO AI:
```text
<PERSONA>
You are an expert DevOps engineer and Python architect.
</PERSONA>

<OBJECTIVE>
Export the current BEACON dashboard project into a standalone deployment script and a `.tar.gz` structure targeting `~/repos/school-weather-with-eap`.

1. Generate a bash script (`setup_school_dashboard.sh`) that will:
   - Create the directory `~/repos/school-weather-with-eap`.
   - Generate the `.env` file prompting the user for API keys and an `ADMIN_PASSWORD`.
   - Install required Python dependencies locally if needed.

2. Configure the deployment specifically for a School EAP environment:
   - Ensure `docker-compose.yml` uses `network_mode: "host"` so the Python backend can intercept IANA Multicast packets for EAP (Emergency Action Plan) protocols.
   - Confirm that `slim_proxy.py` targets `127.0.0.1:5000` (since Docker DNS is disabled in host mode).

3. Ensure the UI nomenclature reflects its environment:
   - The legacy "JoeyAdmin" dashboard should be completely referred to and routed as `/cooladmin`.
   - The script should instruct the user to visit `http://<ip>:8080/cooladmin` to set up their Multicast IP, Port, and EAP Profile (e.g. "I Love U Guys").

Generate the full `setup_school_dashboard.sh` file with embedded base64 or heredoc file creations so I can run a single command to deploy the entire school dashboard instance!
```