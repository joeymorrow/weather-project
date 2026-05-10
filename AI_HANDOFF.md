# 🤖 AI Handoff & Project Context
Welcome to the **Morrow Edge | BEACON** project! This document serves as the "brain state" so any new instance of an AI coding assistant can get fully up to speed with our architecture, design patterns, and strict project rules.

## 🏗️ Project Overview
BEACON is a local weather and scheduling dashboard powered by the Gemini AI API, serving Sault Ste. Marie, Michigan. 
It operates two distinct frontend views:
1. **`index.html` (The Dashboard):** A beautiful, CSS-heavy, responsive widget dashboard featuring "Buddy", an animated CSS robot. It fetches OpenWeatherMap data and local news, summarizes them via AI, and displays a poetic "Sault Pulse."
2. **`rpg.html` (Buddy's World):** A fully functional, highly optimized 3D WebGL (Three.js) sandbox engine where users can drive a Passat, sail a freighter, buy guitars, swim, and explore low-poly local landmarks.

## ⚙️ Backend Architecture (`app.py`)
- **Framework:** Python, Flask, Gunicorn.
- **Concurrency:** We use background threads (`threading.Thread`) alongside `fcntl` locks to prevent multiple Gunicorn workers from spawning duplicate sync or memory-monitor loops.
- **AI Integration:** Uses `google.genai` SDK. The AI adopts the persona of a masterful, charismatic presidential speechwriter focused on the "indomitable human spirit."
- **Data Persistence:** SQLite is used for `pulse_history.db` and `system_logs.db`. Local JSON (`buddy_state.json`) handles state persistence across device reloads.
- **Memory Monitoring:** `tracemalloc` actively watches the heap and automatically triages severe memory leaks via an AI evaluation before sending an emergency SMTP email.

## 🎮 The 3D Engine (`rpg.html`)
The WebGL environment strictly adheres to a "Valgrind-like" zero-allocation mindset.
- **Zero-GC Render Loop:** We absolutely DO NOT use `new` allocations, `.find()`, `.forEach()`, or anonymous closures inside the 60fps `animate()` loop. Everything is pre-allocated or uses C-style `for` loops to prevent the Javascript Garbage Collector from stuttering the game.
- **GPU Instancing:** Clouds, trees, and mountains use `THREE.InstancedMesh` and share a global `dummyObj` matrix calculator. Materials are cached via a `getMat()` dictionary.
- **Frustum Culling & Shadows:** We dynamically check `THREE.Frustum` boundaries for respawning coins to prevent immersion-breaking "pop-in". Shadows use `THREE.PCFSoftShadowMap` with a tightly constrained frustum to optimize the GPU.
- **Procedural Assets:** We use `CanvasTexture` for signs and `AudioContext` for dynamic sound generation (e.g., guitar pentatonic scales, 45hz dubstep saws, freighter horn square waves). Zero external images or MP3s are loaded.
- **Touch UI:** Dynamic viewport height (`100dvh`), `clamp()`, and smart hardware ID tracking for multi-touch joystick/camera panning. Controls are fully cross-compatible with Keyboards, Touchscreens, and TV Remotes (via captured Numpad keycodes).

## 🚀 Deployment Rules
- **No local `docker compose up`:** Deployment is strictly managed via GitHub Actions (`.github/workflows/deploy.yml`) on a self-hosted runner.
- **Secrets Management:** The runner pulls keys (`GEMINI_API_KEY`, etc.) and dynamically injects them into the `.env` file during deployment.
- **Cloudflare Tunnels:** Remote access is securely piped through a `cloudflared` daemon. 

## 📚 AI Documentation Protocol (The Docs Hierarchy)
Whenever you modify a user-facing view, feature, or admin portal (excluding `/rpg`), you MUST review and update the corresponding documentation.
- Documentation resides in the `docs/` directory as Markdown files.
- The global hierarchy starts at `docs/index.md`.
- Public dashboards (`/`, schools) link to docs via a `?` tooltip.
- Admin pages link to docs via a "Documentation" hyperlink.
If you create a new page or new feature, create its documentation file and link it into `docs/index.md`.

## 🗺️ Where to Start
If I ask you to build a new feature or fix a bug, refer back to these patterns. Keep the AI prompts tightly constrained to the actual weather payload, and keep the WebGL engine relentlessly optimized. Let's build!