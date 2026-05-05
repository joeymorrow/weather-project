from dotenv import load_dotenv
load_dotenv()
import os, requests, threading, time, json, re
from flask import Flask, render_template, jsonify
from datetime import datetime
import google.genai as genai
import pytz

app = Flask(__name__)
TZ = pytz.timezone('America/Detroit')
G_KEY = os.environ.get("GEMINI_API_KEY", "")
OWM_KEY = os.environ.get("OPENWEATHERMAP_API_KEY", "")
STATE_FILE = "buddy_state.json"
manual_override = None
override_expiry = 0

state = {
    "temp": 0, "suggestion": "Initializing...", "station": "office", 
    "desc": "Syncing...", "high": 0, "low": 0, "date": "", "time": "--", "icon": "01d",
    "bubble": "...", "pulse": "Anchoring Sault Pulse...",
    "forecast": "Loading forecast...", "acc_css": "none", "is_sleeping": False, "show_bed": False
}

if os.path.exists(STATE_FILE):
    try:
        with open(STATE_FILE, 'r') as f:
            state.update(json.load(f))
    except: pass

def get_best_models():
    """Augmented discovery prioritizing high-RPD models (Flash/Lite)."""
    try:
        client = genai.Client(api_key=G_KEY)
        all_m = list(client.models.list())
        ranked = []
        for m in all_m:
            n = m.name.lower()
            score = 0
            if "3.1-flash-lite" in n: score = 2000
            elif "2.5-flash-lite" in n: score = 1500
            elif "3-flash" in n: score = 1000
            elif "2.5-flash" in n: score = 800
            elif "1.5-flash" in n: score = 500
            if "pro" in n or "ultra" in n: score -= 5000
            if score > 0: ranked.append((m.name, score))
        ranked.sort(key=lambda x: x[1], reverse=True)
        return [r[0] for r in ranked] if ranked else ["models/gemini-2.1-flash-lite", "models/gemini-1.5-flash"]
    except: return ["models/gemini-2.1-flash-lite", "models/gemini-1.5-flash"]

def run_sync():
    global state
    try:
        w = requests.get(f"https://api.openweathermap.org/data/2.5/weather?q=Sault+Ste.+Marie,MI,US&appid={OWM_KEY}&units=imperial", timeout=10).json()
        f = requests.get(f"https://api.openweathermap.org/data/2.5/forecast?q=Sault+Ste.+Marie,MI,US&appid={OWM_KEY}&units=imperial", timeout=10).json()
        now = datetime.now(TZ)
        h = now.hour
        is_sleep = (h >= 22 or h < 6)
        st_id = "bed" if is_sleep else next((v for k,v in {21:"kitchen", 20:"garage", 19:"library", 17:"store", 16:"gym", 8:"office", 6:"coffee"}.items() if h >= k), "coffee")

        global manual_override, override_expiry
        if manual_override and time.time() < override_expiry:
            st_id = manual_override
            is_sleep = (st_id == "bed")
        else:
            manual_override = None

        client = genai.Client(api_key=G_KEY)
        forecast_context = ", ".join([f"{i['dt_txt'].split(' ')[1][:5]} {i['weather'][0]['description']} {int(i['main']['temp'])}F" for i in f['list'][:8]])
        time_str = now.strftime('%I:%M %p')
        prompt = f"""
        Sault MI. Time: {time_str}. Weather: {w['weather'][0]['description']}. Forecast: {forecast_context}. Station: {st_id}. Sleep: {is_sleep}.
        Task 1 (Buddy): 3-5 word technical activity (Passat maintenance, lab coding).
        Task 2 (Pulse): 1-sentence sleek, minimalist status update on the city's current rhythm. Use crisp, modern phrasing suited for a high-tech UI, focusing on objective urban activity. Reserve weather mentions strictly for severe events.
        Task 3 (Forecast): 1 short sentence summarizing today/tomorrow's weather based on forecast.
        Task 4 (Attire): 2-4 word practical clothing or gear suggestion based on the forecast.
        Return JSON: {{ "tip": "attire", "say": "task", "pulse": "vibe", "acc": "tool/none", "forecast": "summary" }}
        """
        
        success = False
        for m_id in get_best_models():
            try:
                resp = client.models.generate_content(model=m_id, contents=prompt)
                ai = json.loads(re.sub(r'```json|```', '', resp.text).strip())
                state.update({
                    "suggestion": ai.get("tip"), "bubble": ai.get("say"), 
                    "pulse": ai.get("pulse"), "acc_css": "zzz" if is_sleep else ai.get("acc", "none"),
                    "forecast": ai.get("forecast", "Weather data processing...")
                })
                print(f"[API] Success via {m_id}", flush=True)
                success = True; break
            except: continue
        
        if not success: state["bubble"] = "Optimizing antenna..."

        day = now.day
        suffix = 'th' if 11 <= day <= 13 else {1:'st', 2:'nd', 3:'rd'}.get(day % 10, 'th')
        state.update({
            "temp": int(w['main']['temp']), "high": int(max([i['main']['temp_max'] for i in f['list'][:8]])), 
            "low": int(min([i['main']['temp_min'] for i in f['list'][:8]])),
            "desc": w['weather'][0]['description'].title(), "icon": w['weather'][0]['icon'],
            "date": now.strftime(f"%A, %B {day}{suffix}, %Y"), "time": now.strftime('%I:%M %p'), 
            "station": st_id, "is_sleeping": is_sleep, "show_bed": (st_id == "bed" or h >= 21 or h < 6)
        })
        with open(STATE_FILE, 'w') as sf: json.dump(state, sf)
    except Exception as e: 
        print(f"[ERROR] {e}", flush=True)
        state["bubble"] = "Signal degraded. Retrying..."

def sync_loop():
    while True:
        run_sync()
        time.sleep(600)

@app.route('/')
def index(): return render_template('index.html', build_timestamp=os.environ.get("BUILD_TIMESTAMP", "Local Dev"), **state)
@app.route('/api/state')
def get_state(): 
    out = state.copy()
    out["build_timestamp"] = os.environ.get("BUILD_TIMESTAMP", "Local Dev")
    return jsonify(out)

@app.route('/api/move/<station>')
def move_buddy(station):
    global state, manual_override, override_expiry
    manual_override = station
    override_expiry = time.time() + 3600 # Manual override lasts 1 hour
    now = datetime.now(TZ)
    state.update({"station": station, "is_sleeping": (station == "bed"), "bubble": "Rerouting...", "acc_css": "none" if station != "bed" else "zzz", "show_bed": (station == "bed" or now.hour >= 21 or now.hour < 6)})
    threading.Thread(target=run_sync, daemon=True).start()
    return jsonify(success=True)

if __name__ == '__main__':
    threading.Thread(target=sync_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, debug=False)
