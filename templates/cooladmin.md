# CoolAdmin Interface

The CoolAdmin dashboard is the ultimate system overlord interface. It is designed for system administrators to manage the underlying Docker/host environments and active endpoints.

## Key Features
*   **Core Services:** Check the live `systemctl` status of Docker, Cloudflared, and Cron on the host machine.
*   **Endpoint Management:** Enable, disable, or completely delete dynamic endpoint URLs (e.g., `/schools/brimley`).
*   **EAP Multicast Subscriptions:** Configure local UDP listeners to ingest emergency alerts directly from your facility's network.
*   **Hallucination Cleanup:** Audit the AI's "Pulse" archives and manually trigger the script to delete hallucinated/fake events from the database.
*   **Resource Telemetry:** View live graphs of system memory and load averages over the last 7 days to identify memory leaks.