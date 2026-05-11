# 🏗️ BEACON Pipeline & Architecture

This document visually outlines the CI/CD pipeline, architecture, and deployment strategy for **Morrow Edge | BEACON**.

## 1. Textual Visual (ASCII Flow)

```text
=============================================================================================
                                  BEACON CI/CD PIPELINE
=============================================================================================

 [ 💻 Local Dev ]            [ 🐙 Source Control ]               [ ⚙️ CI/QA (Self-Hosted) ]
 VS Code + Gemini                Git Push                      GitHub Actions Runner
 (Py/HTML/CSS/3JS) ────────────> (Branch: main) ─────────────> ├─> 1. Flake8 Linting
                                                               ├─> 2. SAST/DAST & Secret Leak Detection
                                                               ├─> 3. compileall Syntax Check
                                                               ├─> 4. Jinja2 Validation
                                                               ├─> 5. Puppeteer UI & Modals
                                                               └─> 6. Docker Config Check
                                                                          │
                                                                          ▼
 [ 🌍 Edge Delivery ]        [ ☁️ Tunneling ]                    [ 🚀 Deployment ]
 MorrowEdge.com                Cloudflare                      ├─> 1. Inject Secrets (.env)
 TVs / Displays    <─────────  Zero Trust      <────────────── ├─> 2. Docker Compose Build
 Mobile & Web                  (cloudflared)                   ├─> 3. Docker Compose Up -d
                                                               └─> 4. Smoke Test (cURL Web/RPG)
=============================================================================================
```

## 2. Mermaid Diagram (GitHub Native)

```mermaid
flowchart TD
    %% Styling
    classDef dev fill:#1e293b,stroke:#38bdf8,stroke-width:2px,color:#fff
    classDef qa fill:#451a03,stroke:#fbbf24,stroke-width:2px,color:#fff
    classDef deploy fill:#14532d,stroke:#4ade80,stroke-width:2px,color:#fff
    classDef edge fill:#312e81,stroke:#818cf8,stroke-width:2px,color:#fff

    subgraph Dev ["1. Local Development"]
        VSC["VS Code + Gemini Extension"]
        Code["Python | HTML | CSS | Three.js"]
        VSC --> Code
    end

    subgraph VC ["2. Source Control"]
        GIT["Git version control"]
        GH["GitHub Repository"]
        Code -- "git push origin main" --> GIT
        GIT --> GH
    end

    subgraph CI ["3. GitHub Actions CI/QA"]
        GHA["GitHub Actions (deploy.yml)"]
        Runner["Self-Hosted Ubuntu Runner"]
        
        QA_Lint["Flake8 / Syntax Tests"]
        QA_Sec["SAST/DAST & Secret Leak Detection"]
        QA_Jinja["Jinja2 Template Check"]
        QA_Puppet["Puppeteer Headless UI Check"]
        QA_Docker["Docker Config Validation"]

        GH --> GHA
        GHA --> Runner
        Runner --> QA_Lint
        Runner --> QA_Sec
        Runner --> QA_Jinja
        Runner --> QA_Puppet
        Runner --> QA_Docker
    end

    subgraph CD ["4. Deployment"]
        Env["Construct .env via Secrets"]
        DockerBuild["Docker Compose Build"]
        DockerRun["Docker Compose Up"]
        Smoke["Post-Deploy Smoke Test"]

        QA_Docker --> Env
        Env --> DockerBuild
        DockerBuild --> DockerRun
        DockerRun --> Smoke
    end

    subgraph Prod ["5. Edge Delivery"]
        CF["Cloudflare Tunnel (cloudflared)"]
        Domain["MorrowEdge.com"]
        Display["End Users / Local TV Displays"]

        Smoke --> CF
        CF --> Domain
        Domain --> Display
    end

    %% Apply classes
    class VSC,Code dev;
    class GIT,GH dev;
    class GHA,Runner qa;
    class QA_Lint,QA_Jinja,QA_Puppet,QA_Docker qa;
    class Env,DockerBuild,DockerRun,Smoke deploy;
    class CF,Domain,Display edge;
```

---
*Note: Deployments are handled exclusively by the GitHub Runner. Manual `docker compose up` is not supported on the host machine to maintain CI integrity.*