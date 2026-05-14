# 🛠️ BEACON Future-Proofing & "Rich Man Problems"

This file tracks architectural debt, scaling upgrades, and "nice-to-have" enterprise features that we will tackle once the core feature set is complete and we need to handle massive scale.

## 🚀 Infrastructure & Scalability
- [ ] **Flask-SSE / WebSockets:** Upgrade the frontend dashboard polling (`setInterval`) to use Server-Sent Events. This will push data diffs to the client (like H.264 video frames) instead of the client repeatedly asking the server for the whole table. Drastically reduces server load and SQLite contention at scale.
- [ ] **PostgreSQL / MySQL Migration:** Move off of SQLite once we start handling hundreds of schools and constant concurrent writes. SQLite is amazing for our current single-server edge node, but distributed scale requires a real RDBMS.
- [ ] **Redis Job Queue:** Move the `job_queue` table from SQLite into Redis using Celery or RQ for true enterprise-grade asynchronous background workers.

## 🧠 AI & Logic
- [ ] **Vector Database for Memory:** Instead of basic string-matching for deduplicating pulses and checking history, implement a lightweight Vector DB (like ChromaDB or pgvector) to give Buddy actual long-term semantic memory.
- [ ] **Model Auto-Routing (Cost Optimization):** Dynamically route simpler 5W extraction tasks to highly efficient models (like `gemini-1.5-flash-8b` or local Ollama instances), and reserve heavier reasoning for creative pulse generation.
- [ ] **AD/Entra Group Sync:** Automate RBAC user provisioning by syncing security groups directly from Microsoft Entra ID or On-Prem Active Directory.

## 🌐 Endpoints & Edge
- [ ] **Global CDN Edge Caching:** Cache the OpenWeatherMap payload representations at the Cloudflare edge so thousands of concurrent TVs don't even hit our server for weather updates.
- [ ] **Multi-Region D-Bus / IPC:** Abstract the local D-Bus IPC into a gRPC or ZeroMQ service if the UI and worker nodes eventually need to be split across separate physical hardware.

## 🖥️ UI & Quality of Life
- [ ] **Drag & Drop Slide Ordering:** Replace the "Up/Down" buttons in the admin panel with a native HTML5 drag-and-drop sortable list for the slides.
- [ ] **Granular Analytics Dashboards:** Integrate Grafana or a richer Chart.js dashboard for API usage breakdowns over longer periods (30-90 days).