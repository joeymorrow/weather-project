from dotenv import load_dotenv
load_dotenv()
import os, requests, threading, time, json, re
from flask import Flask, render_template, jsonify
from datetime import datetime
import google.genai as genai
from google.genai import types
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
    "forecast": "Loading forecast...", "acc_css": "none", "is_sleeping": False, "show_bed": False,
    "is_day": False, "is_golden": False, "pop": 0, "pulse_history": []
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
        
        # Calculate dynamic daylight state and precipitation
        sunrise = w['sys']['sunrise']
        sunset = w['sys']['sunset']
        now_ts = now.timestamp()
        is_golden = (sunrise - 900 <= now_ts < sunrise + 2700) or (sunset - 2700 <= now_ts < sunset + 900)
        is_day = (sunrise + 2700) <= now_ts < (sunset - 2700)
        pop = int(f['list'][0].get('pop', 0) * 100)

        global manual_override, override_expiry
        if manual_override and time.time() < override_expiry:
            st_id = manual_override
            is_sleep = (st_id == "bed")
        else:
            manual_override = None

        client = genai.Client(api_key=G_KEY)
        forecast_context = ", ".join([f"{i['dt_txt'].split(' ')[1][:5]} {i['weather'][0]['description']} {int(i['main']['temp'])}F" for i in f['list'][:8]])
        time_str = now.strftime('%I:%M %p')
        date_str = now.strftime('%B %d')
        prompt = f"""
        Sault MI. Date: {date_str}. Time: {time_str}. Weather: {w['weather'][0]['description']}. Precip Chance: {pop}%. Forecast: {forecast_context}. Station: {st_id}. Sleep: {is_sleep}.
        Task 1 (Buddy): 3-5 word technical activity (Passat maintenance, lab coding).
        Task 2 (Pulse): 1-sentence sleek, minimalist status update on the city's current rhythm. Sometimes search for and fold in real, ultra-recent local news, events, or business openings in Sault Ste. Marie, MI or Sugar Island (e.g., new spots like Del Mar, Hill Top bar reopening). If no recent news stands out, default to the general vibe. Use crisp, modern phrasing suited for a high-tech UI. Keep it chill, warm, and subtly optimistic. Refer to the city as "the Sault" or "the Soo". If you successfully folded in real local news/events, set the JSON boolean "is_news" to true. Otherwise, false.
        Task 3 (Forecast): 1 short sentence summarizing today/tomorrow's weather based on forecast.
        Task 4 (Attire): 2-4 word practical clothing or gear suggestion based on the forecast. Factor in the current season ({now.strftime('%B')}) to match real-world wardrobe habits (e.g., favor layers or rain jackets over heavy winter boots in spring/summer).
        Return JSON: {{ "tip": "attire", "say": "task", "pulse": "vibe", "acc": "tool/none", "forecast": "summary", "is_news": true }}
        """
        
        success = False
        for m_id in get_best_models():
            try:
                resp = client.models.generate_content(
                    model=m_id, 
                    contents=prompt,
                    config=types.GenerateContentConfig(tools=[{"google_search": {}}])
                )
                text = resp.text
                json_str = text[text.find('{'):text.rfind('}')+1]
                ai = json.loads(json_str)
                
                new_pulse = ai.get("pulse", "Anchoring Sault Pulse...")
                is_news = str(ai.get("is_news", False)).lower() in ["true", "1", "yes"]
                hist = state.get("pulse_history", [])
                
                if is_news and (not hist or hist[0].get("text") != new_pulse):
                    hist.insert(0, {"date": date_str, "text": new_pulse})
                    hist = hist[:21] # Retain exactly the last 21 grounded pulses
                
                state.update({
                    "suggestion": ai.get("tip"), "bubble": ai.get("say"), 
                    "pulse": new_pulse, "acc_css": "zzz" if is_sleep else ai.get("acc", "none"),
                    "forecast": ai.get("forecast", "Weather data processing..."),
                    "pulse_history": hist
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
            "station": st_id, "is_sleeping": is_sleep, "show_bed": (st_id == "bed" or h >= 21 or h < 6),
            "is_day": is_day, "is_golden": is_golden, "pop": pop
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
def index(): return render_template('index.html', build_timestamp=os.environ.get("BUILD_TIMESTAMP", "Local Dev"), **state.copy())
@app.route('/api/state')
def get_state(): 
    out = state.copy()
    out["build_timestamp"] = os.environ.get("BUILD_TIMESTAMP", "Local Dev")
    return jsonify(out)

@app.route('/rpg')
def rpg():
    now = datetime.now(TZ)
    month = now.month
    # Determine season colors (Hex)
    if month in [12, 1, 2]: season_data = {"terrain": "0xfffafa", "leaves": "0xeeeeee", "season": "Winter"} # Snow
    elif month in [3, 4, 5]: season_data = {"terrain": "0x7cfc00", "leaves": "0xff69b4", "season": "Spring"} # Light green, pink buds
    elif month in [9, 10, 11]: season_data = {"terrain": "0x8b4513", "leaves": "0xd2691e", "season": "Autumn"} # Brown grass, orange leaves
    else: season_data = {"terrain": "0x228b22", "leaves": "0x006400", "season": "Summer"} # Deep green
    
    return render_template('rpg.html', 
                           terrain_color=season_data["terrain"], 
                           leaf_color=season_data["leaves"],
                           season_name=season_data["season"],
                           **state.copy())

@app.route('/api/move/<station>')
def move_buddy(station):
    global state, manual_override, override_expiry
    manual_override = station
    override_expiry = time.time() + 3600 # Manual override lasts 1 hour
    now = datetime.now(TZ)
    
    # Local fallback responses so web interactions don't trigger expensive API calls
    bubbles = {
        "coffee": "Grabbing a brew...", "office": "Compiling data...", 
        "gym": "Gaining processing power...", "store": "Running errands...", 
        "library": "Parsing archives...", "garage": "Diagnostic mode...", 
        "park": "Nature protocol engaged...", "kitchen": "Refueling...", 
        "bed": "Powering down..."
    }
    
    state.update({"station": station, "is_sleeping": (station == "bed"), "bubble": bubbles.get(station, "Rerouting..."), "acc_css": "none" if station != "bed" else "zzz", "show_bed": (station == "bed" or now.hour >= 21 or now.hour < 6)})
    
    # Save state locally so the web UI updates instantly across all connected TVs
    try:
        with open(STATE_FILE, 'w') as sf: json.dump(state, sf)
    except: pass
    
    return jsonify(success=True)

# When run with Gunicorn, this starts the sync loop without blocking the workers
if __name__ != '__main__':
    threading.Thread(target=sync_loop, daemon=True).start()

if __name__ == '__main__':
    threading.Thread(target=sync_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, debug=False)
