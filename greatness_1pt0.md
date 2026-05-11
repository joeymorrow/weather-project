# 🚀 BEACON Buddy V2: Architectural Refactor Plan

This document serves as the structural roadmap to trim the footprint, reduce cognitive load, and streamline deployments without sacrificing any of the hardcore performance optimizations (like WebGL frustum culling and bitwise logic) that make this project great.

## 1. Decouple the Backend (Kill the Megascript)
- **Current State:** `app.py` is over 1,400 lines long. It handles web serving, the database ORM, AI prompting, D-Bus interception, and background task management simultaneously.
- **Action:** Split into a modular Flask architecture.
  - `routes/`: Dedicated files for `/api`, `/admin`, and frontend delivery.
  - `services/`: Isolated classes for `GeminiService`, `WeatherService`, and `EapMulticastService`.
  - `models.py`: Implement an ORM (like SQLAlchemy or Peewee) to stabilize schema migrations and replace raw `sqlite3` execution blocks.

## 2. Separate Web Workers from Background Tasks
- **Current State:** `app.py` launches background threads (`sync_loop`, `monitor_loop`, `eap_multicast_listener`) directly on boot. Because Gunicorn spawns multiple workers, complex POSIX file locks (`filelock`) are required to prevent four workers from running four identical overlapping loops.
- **Action:** The web server should *only* serve HTTP. Extract all `while True:` background loops into a single, separate entry point (e.g., `worker.py`).
- **Benefit:** Docker Compose will run one `web` service and one `worker` service. This completely deletes the complex file-locking logic, reduces the memory footprint of web workers, and eliminates race conditions.

## 3. State Management (Drop the JSON File)
- **Current State:** Global state is managed via an in-memory dictionary protected by a `threading.Lock()`, which periodically dumps to `buddy_state.json`.
- **Action:** Replace this entirely with **Redis** (or SQLite's in-memory KV features if minimizing stack size is a priority).
- **Benefit:** Redis natively handles atomic operations, allowing us to delete `state_lock` completely. Web routes, APIs, and background workers can all read/write state instantly without file I/O bottlenecks.

## 4. Modularize the Frontend
- **Current State:** `index.html` and `rpg.html` are monolithic (>1000 lines each) containing massive HTML, inline CSS, and complex JavaScript engines.
- **Action:** Extract the JavaScript into `static/js/buddy_engine.js` and `static/js/rpg_engine.js`. Extract the CSS into `static/css/styles.css`.
- **Benefit:** Browsers aggressively cache `.js` and `.css` files. Separating them trims the network payload size dramatically on reloads and makes IDE navigation significantly easier.

## 5. Streamline the CI/CD Pipeline (`deploy.yml`)
- **Current State:** The GitHub Actions pipeline runs a complex 150+ line raw Node.js Puppeteer script inline inside a Bash shell on the host OS.
- **Action:**
  1. **Extract the Tests:** Move that Puppeteer script into a dedicated testing file like `tests/qa_suite.js`.
  2. **Test the Container:** Build the Docker image *first*, start it, and run the tests against the live container rather than booting Flask natively on the GitHub Runner.
  3. **Multi-Stage Docker Builds:** Compile dependencies in an interim image and only copy the compiled results to the final image, drastically reducing the deployed container size on edge devices.

---
*The goal is not to change what the code does, but where it lives. Breaking the monolith into discrete, single-responsibility services drops cognitive load to zero and makes the deployment pipeline lightning fast.*