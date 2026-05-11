#!/usr/bin/env python3
import os
import sys
import json
import platform
import subprocess
import shutil
import secrets
import getpass

STATE_FILE = "setup_state.json"
ENV_FILE = ".env"

def run_command(cmd, shell=False, check=False):
    cmd_str = cmd if isinstance(cmd, str) else ' '.join(cmd)
    print(f"Running: {cmd_str}")
    try:
        subprocess.run(cmd, shell=shell, check=check)
    except Exception as e:
        print(f"Error running command: {e}")

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except:
            return {"installed_services": [], "created_files": []}
    return {"installed_services": [], "created_files": []}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=4)

def prompt_yes_no(question, default="y"):
    if os.environ.get("CI") == "true":
        return default.lower() == 'y'
    default_text = " [Y/n]" if default.lower() == 'y' else " [y/N]"
    while True:
        ans = input(f"{question}{default_text}: ").strip().lower()
        if not ans:
            ans = default.lower()
        if ans in ['y', 'yes']:
            return True
        if ans in ['n', 'no']:
            return False

def prompt_string(question, default="", hide=False):
    if os.environ.get("CI") == "true":
        return default
    default_text = f" [{default}]" if default and not hide else (" [***]" if default and hide else "")
    while True:
        if hide:
            ans = getpass.getpass(f"{question}{default_text}: ").strip()
        else:
            ans = input(f"{question}{default_text}: ").strip()

        if not ans and default:
            return default
        if ans:
            return ans
        if not default:
            print("This field is required.")

def install():
    state = load_state()
    if "created_files" not in state: state["created_files"] = []
    if "installed_services" not in state: state["installed_services"] = []

    print("=========================================")
    print("    BEACON Environment Setup Wizard")
    print("=========================================")
    print(f"Detected Platform: {platform.system()} ({platform.machine()})\n")

    # 1. Environment Variables
    if prompt_yes_no("Configure environment variables (.env)?", "y"):
        env_vars = {}
        if os.path.exists(ENV_FILE):
            with open(ENV_FILE, 'r') as f:
                for line in f:
                    if '=' in line and not line.startswith('#'):
                        k, v = line.strip().split('=', 1)
                        env_vars[k] = v.strip('"\'')
        
        env_vars["GEMINI_API_KEY"] = prompt_string("Enter Gemini API Key", env_vars.get("GEMINI_API_KEY", ""), hide=True)
        env_vars["OPENWEATHERMAP_API_KEY"] = prompt_string("Enter OpenWeatherMap API Key", env_vars.get("OPENWEATHERMAP_API_KEY", ""), hide=True)
        env_vars["ADMIN_USERNAME"] = prompt_string("Admin Username", env_vars.get("ADMIN_USERNAME", "admin"))
        env_vars["ADMIN_PASSWORD"] = prompt_string("Admin Password", env_vars.get("ADMIN_PASSWORD", "changeme"), hide=True)
        
        if "INTERNAL_API_SECRET" not in env_vars:
            env_vars["INTERNAL_API_SECRET"] = secrets.token_hex(32)

        with open(ENV_FILE, 'w') as f:
            for k, v in env_vars.items():
                f.write(f"{k}={v}\n")

        if ENV_FILE not in state["created_files"]:
            state["created_files"].append(ENV_FILE)

        print("✅ .env file successfully configured.\n")

    # 2. Virtual Environment & Requirements
    if prompt_yes_no("Set up Python virtual environment and install pip dependencies?", "y"):
        venv_dir = "venv"
        if not os.path.exists(venv_dir):
            print("Creating virtual environment...")
            run_command([sys.executable, "-m", "venv", venv_dir])
            if venv_dir not in state["created_files"]:
                state["created_files"].append(venv_dir)
        
        pip_cmd = os.path.join(venv_dir, "Scripts", "pip") if platform.system() == "Windows" else os.path.join(venv_dir, "bin", "pip")
        print("Installing dependencies...")
        run_command([pip_cmd, "install", "flask", "requests", "google-genai", "pytz", "python-dotenv", "filelock", "psutil", "msal", "markdown", "Werkzeug", "beautifulsoup4"])
        if os.path.exists("requirements.txt"):
            run_command([pip_cmd, "install", "-r", "requirements.txt"])

        print("✅ Python dependencies installed.\n")

    # 3. OS-Specific Services
    if platform.system() == "Linux":
        if prompt_yes_no("Install D-Bus Listener Service? (Optional, Linux only, requires sudo)", "y"):
            if os.path.exists("install_dbus_service.sh"):
                run_command(["sudo", "bash", "install_dbus_service.sh"])
                if "beacon-dbus.service" not in state["installed_services"]:
                    state["installed_services"].append("beacon-dbus.service")
            else:
                print("install_dbus_service.sh not found. Skipping.")
        
        if prompt_yes_no("Install Cloudflare Tunnel (cloudflared)? (Optional, for remote access)", "n"):
            token = prompt_string("Enter Cloudflare Tunnel Token")
            print("Downloading cloudflared...")
            run_command("curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb", shell=True)
            run_command(["sudo", "dpkg", "-i", "cloudflared.deb"])
            run_command(["sudo", "cloudflared", "service", "install", token])
            if "cloudflared" not in state["installed_services"]:
                state["installed_services"].append("cloudflared")
            if "cloudflared.deb" not in state["created_files"]:
                state["created_files"].append("cloudflared.deb")
    else:
        print(f"Skipping Linux-specific system services (D-Bus, Cloudflared) on {platform.system()}.\n")

    save_state(state)
    print("=========================================")
    print("🎉 Setup Complete!")
    print("To start the app manually, activate the virtual environment and run app.py.")
    print("=========================================")

def uninstall():
    state = load_state()
    print("=========================================")
    print("    BEACON Environment Uninstall")
    print("=========================================")

    if not state.get("created_files") and not state.get("installed_services"):
        print("No installation state found. Nothing to uninstall.")
        return

    if not prompt_yes_no("WARNING: This will remove the .env file, virtual environment, and disable installed services tracked by this script. Proceed?", "n"):
        print("Uninstall cancelled.")
        return

    for service in state.get("installed_services", []):
        if platform.system() == "Linux":
            print(f"Stopping and disabling service: {service}")
            run_command(["sudo", "systemctl", "stop", service])
            run_command(["sudo", "systemctl", "disable", service])
            if service == "cloudflared":
                run_command(["sudo", "cloudflared", "service", "uninstall"])

    for file_path in state.get("created_files", []):
        if os.path.exists(file_path):
            print(f"Removing {file_path}")
            shutil.rmtree(file_path) if os.path.isdir(file_path) else os.remove(file_path)

    if os.path.exists(STATE_FILE): os.remove(STATE_FILE)
    print("✅ Uninstall complete.")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--uninstall":
        uninstall()
    else:
        install()