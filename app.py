from dotenv import load_dotenv
load_dotenv()
import os, requests, threading, time, json, re, sqlite3
import tracemalloc
import fcntl
from contextlib import closing
from flask import Flask, render_template, jsonify
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
import google.genai as genai
from google.genai import types
import pytz

app = Flask(__name__)
TZ = pytz.timezone('America/Detroit')
G_KEY = os.environ.get("GEMINI_API_KEY", "")
OWM_KEY = os.environ.get("OPENWEATHERMAP_API_KEY", "")
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.path.join(DATA_DIR, "buddy_state.json")
DB_FILE = os.path.join(DATA_DIR, "pulse_history.db")
LOG_DB_FILE = os.path.join(DATA_DIR, "system_logs.db")
manual_override = None
override_expiry = 0
state_lock = threading.Lock()

def init_db():
    with closing(sqlite3.connect(DB_FILE, timeout=10)) as pulse_conn:
        with pulse_conn:
            pulse_conn.execute('''CREATE TABLE IF NOT EXISTS pulses (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                date TEXT,
                                text TEXT UNIQUE
                             )''')
    with closing(sqlite3.connect(LOG_DB_FILE, timeout=10)) as log_conn:
        with log_conn:
            log_conn.execute('''CREATE TABLE IF NOT EXISTS logs (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                                log_type TEXT,
                                message TEXT,
                                details TEXT
                             )''')
init_db()

def load_history():
    try:
        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
            c = conn.cursor()
            c.execute("SELECT date, text FROM pulses ORDER BY id DESC LIMIT 21")
            return [{"date": r[0], "text": r[1]} for r in c.fetchall()]
    except:
        return []

def log_system_event(log_type, message, details=""):
    try:
        with closing(sqlite3.connect(LOG_DB_FILE, timeout=10)) as conn:
            with conn:
                conn.execute("INSERT INTO logs (log_type, message, details) VALUES (?, ?, ?)",
                             (log_type, message, json.dumps(details) if isinstance(details, dict) else str(details)))
    except Exception as e:
        print(f"[ERROR] Failed to write to system log: {e}", flush=True)

state = {
    "temp": 0, "suggestion": "Initializing...", "station": "office", 
    "desc": "Syncing...", "high": 0, "low": 0, "date": "", "time": "--", "icon": "01d",
    "bubble": "...", "pulse": "Anchoring Sault Pulse...",
    "forecast": "Loading forecast...", "acc_css": "none", "is_sleeping": False, "show_bed": False,
    "is_day": False, "is_golden": False, "pop": 0, "pulse_history": load_history()
}

if os.path.exists(STATE_FILE):
    try:
        with open(STATE_FILE, 'r') as f:
            with state_lock:
                state.update(json.load(f))
    except: pass

gemini_client = None

def get_best_models():
    """Augmented discovery prioritizing high-RPD models (Flash/Lite)."""
    global gemini_client
    try:
        if not gemini_client: gemini_client = genai.Client(api_key=G_KEY)
        all_m = list(gemini_client.models.list())
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

        global gemini_client
        if not gemini_client: gemini_client = genai.Client(api_key=G_KEY)
        forecast_context = ", ".join([f"{i['dt_txt'].split(' ')[1][:5]} {i['weather'][0]['description']} {int(i['main']['temp'])}F" for i in f['list'][:8]])
        time_str = now.strftime('%I:%M %p')
        date_str = now.strftime('%B %d')
        
        is_late_night = (now.hour == 21 and now.minute >= 30) or (now.hour >= 22)
        pulse_task = "Task 2 (Pulse): 1-sentence sleek summary of tomorrow's weather forecast. Start with 'Tomorrow:'. Make it conversational, descriptive, and highly engaging. Do not search for news. Set 'is_news' to false." if is_late_night else "Task 2 (Pulse): Adopt the persona of a veteran local author, seasoned regional broadcaster, and passionate Sault historian. Your career is built on your deep learning capability—observing the changing weather, community events, and subtle shifts in the Upper Peninsula to draw profound, moving connections. Provide a flowing, companionable train of thought on the region's true rhythm. Length is flexible—take the space you need to write something genuinely exceptional. Do NOT get stuck repeating the same topics. Be highly varied and imaginative based on the actual weather, time, and local news in Sault Ste. Marie or the EUP. Examples of the VIBE (do not copy literally unless true): 'A full flight is landing at CIU tonight...', 'Sherman Park Beach will be packed today.', 'The Kincheloe garage sale starts Friday.', or 'Take a quiet drive to Mackinac City!'. Ground the update in vivid, atmospheric UP sensory details (e.g., the fog rolling off the St. Marys River, the crunch of fresh snow, the hum of the highway, or a nod to a local staple like Clyde's or the Sugar Island Ferry). Keep the tone masterful, warm, visually cinematic (peaceful and observant), and positively reinforcing. Refer to the city as 'the Sault' or 'the Soo'. If your update shares a tangible local fact, event, or specific community detail, set the JSON boolean 'is_news' to true. Otherwise, set it to false."

        prompt = f"""
        Sault MI. Date: {date_str}. Time: {time_str}. Weather: {w['weather'][0]['description']}. Precip Chance: {pop}%. Forecast: {forecast_context}. Station: {st_id}. Sleep: {is_sleep}.
        Task 1 (Buddy): 3-5 word technical activity (Passat maintenance, lab coding).
        {pulse_task}
        Task 3 (Forecast): 1 short sentence summarizing today/tomorrow's weather based on forecast.
        Task 4 (Attire): 2-4 word practical clothing or gear suggestion based on the forecast. Factor in the current season ({now.strftime('%B')}) to match real-world wardrobe habits (e.g., favor layers or rain jackets over heavy winter boots in spring/summer).
        Return JSON: {{ "tip": "attire", "say": "task", "pulse": "vibe", "acc": "tool/none", "forecast": "summary", "is_news": true }}
        """
        
        success = False
        for m_id in get_best_models():
            try:
                resp = gemini_client.models.generate_content(
                    model=m_id, 
                    contents=prompt,
                    config=types.GenerateContentConfig(tools=[{"google_search": {}}])
                )
                text = resp.text
                json_str = text[text.find('{'):text.rfind('}')+1]
                ai = json.loads(json_str)
                
                new_pulse = ai.get("pulse", "Anchoring Sault Pulse...")
                is_news = str(ai.get("is_news", False)).lower() in ["true", "1", "yes"]
                
                if is_news:
                    try:
                        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                            with conn:
                                conn.execute("INSERT INTO pulses (date, text) VALUES (?, ?)", (date_str, new_pulse))
                    except sqlite3.IntegrityError:
                        pass # Ignore exact duplicate pulses
                
                hist = load_history()
                
                with state_lock:
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
        with state_lock:
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
        with state_lock: state["bubble"] = "Signal degraded. Retrying..."

def sync_loop():
    # Prevent multiple Gunicorn workers from spawning redundant background threads!
    lock_file = open(os.path.join(DATA_DIR, "sync_loop.lock"), "w")
    try:
        fcntl.lockf(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        return # Another worker is already running the sync loop. Exit quietly.

    while True:
        run_sync()
        time.sleep(600)

def monitor_loop():
    # Prevent multiple Gunicorn workers from spawning redundant monitor threads
    lock_file = open(os.path.join(DATA_DIR, "monitor_loop.lock"), "w")
    try:
        fcntl.lockf(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        return # Another worker is already running the monitor loop.

    tracemalloc.start()
    last_snapshot = tracemalloc.take_snapshot()
    initial_threads = threading.enumerate()
    log_system_event("MONITOR_START", f"Monitoring started with {len(initial_threads)} threads.", {"threads": [t.name for t in initial_threads]})

    last_leak_email_time = 0
    last_leak_signature = ""

    while True:
        time.sleep(1800) # Run every 30 minutes

        # 1. Thread Count Check
        current_threads = threading.enumerate()
        log_system_event("THREAD_COUNT", f"Active threads: {len(current_threads)}", {"threads": [t.name for t in current_threads]})

        # 2. File Descriptor Check
        try:
            fd_count = len(os.listdir('/proc/self/fd'))
            log_system_event("FD_COUNT", f"Open file descriptors: {fd_count}")
        except FileNotFoundError:
            pass # Not on Linux, skip

        # 3. Memory Growth Check
        current_snapshot = tracemalloc.take_snapshot()
        top_stats = current_snapshot.compare_to(last_snapshot, 'lineno')
        total_growth = sum(stat.size_diff for stat in top_stats)
        if total_growth > 1024 * 1024: # Log if memory grew by more than 1MB
            leak_details = [str(stat) for stat in top_stats[:5]]
            log_system_event("MEMORY_GROWTH", f"Memory grew by {total_growth / 1024:.1f} KiB in last 30 mins.", {"top_5_leaks": leak_details})
            
            # Heuristic 1: Deduplication (Ignore if it's the exact same leak signature)
            current_sig = "".join([str(stat.traceback) for stat in top_stats[:3]])
            if current_sig == last_leak_signature:
                last_snapshot = current_snapshot
                continue

            # Heuristic 2: Backoff (Max 1 alert every 12 hours)
            current_time = time.time()
            if current_time - last_leak_email_time < 43200:
                last_snapshot = current_snapshot
                continue

            # Heuristic 3: AI Criticality Check & Triage
            try:
                global gemini_client
                if not gemini_client: gemini_client = genai.Client(api_key=G_KEY) # AI client initialized
                prompt = f"A Python memory leak was detected. Top 5 allocations: {leak_details}. Evaluate if this is a severe, compounding leak or normal background caching. Return JSON only: {{\"critical\": true, \"reason\": \"<why>\", \"prompt_suggestion\": \"<how I should prompt you to fix it>\"}} Or if safe: {{\"critical\": false}}"
                for m_id in get_best_models():
                    try:
                        resp = gemini_client.models.generate_content(model=m_id, contents=prompt)
                        ai_eval = json.loads(resp.text[resp.text.find('{'):resp.text.rfind('}')+1])
                    
                        if ai_eval.get("critical"):
                            msg = MIMEText(f"Reason: {ai_eval.get('reason')}\n\nPaste this into gemini to find the solution: \n{leak_details}\n\nPrompt Suggestion: \n{ai_eval.get('prompt_suggestion')} \n\n(Geared for your specific chat history context!)")
                            msg['Subject'] = "[CRITICAL - BEACON BUDDY] - Memory Leak"
                            msg['From'] = os.environ.get("SMTP_USER", "buddy-alerts@morrowedge.com")
                            msg['To'] = "joseph@morrowedge.com"
                            
                            smtp_server = os.environ.get("SMTP_SERVER", "localhost")
                            smtp_port = int(os.environ.get("SMTP_PORT", 587))
                            with smtplib.SMTP(smtp_server, smtp_port) as server:
                                if os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASS"):
                                    server.starttls()
                                    server.login(os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASS"))
                                server.send_message(msg)
                            log_system_event("EMAIL_SENT", "Sent critical memory leak email to joseph@morrowedge.com")
                            last_leak_email_time = current_time
                            last_leak_signature = current_sig
                        break # AI evaluated successfully, break fallback loop
                    except: continue
            except Exception as e:
                print(f"[ERROR] AI Leak eval/email failed: {e}", flush=True)
                
        last_snapshot = current_snapshot

@app.route('/')
def index():
    with state_lock: return render_template('index.html', build_timestamp=os.environ.get("BUILD_TIMESTAMP", "Local Dev"), **state.copy())
@app.route('/api/state')
def get_state(): 
    with state_lock:
        out = state.copy()
        out["build_timestamp"] = os.environ.get("BUILD_TIMESTAMP", "Local Dev")
        return jsonify(out)

@app.route('/api/system/logs')
def get_system_logs():
    try:
        with closing(sqlite3.connect(LOG_DB_FILE, timeout=10)) as conn:
            c = conn.cursor()
            c.execute("SELECT timestamp, log_type, message, details FROM logs ORDER BY id DESC LIMIT 100")
            logs = [{"timestamp": r[0], "type": r[1], "message": r[2], "details": r[3]} for r in c.fetchall()]
            return jsonify(logs)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/rpg')
def rpg():
    now = datetime.now(TZ)
    month = now.month
    # Determine season colors (Hex)
    if month in [12, 1, 2]: season_data = {"terrain": "0xfffafa", "leaves": "0xeeeeee", "season": "Winter"} # Snow
    elif month in [3, 4, 5]: season_data = {"terrain": "0x7cfc00", "leaves": "0xff69b4", "season": "Spring"} # Light green, pink buds
    elif month in [9, 10, 11]: season_data = {"terrain": "0x8b4513", "leaves": "0xd2691e", "season": "Autumn"} # Brown grass, orange leaves
    else: season_data = {"terrain": "0x228b22", "leaves": "0x006400", "season": "Summer"} # Deep green
    
    is_christmas = (now.month == 12 and now.day == 25)
    
    with state_lock:
        return render_template('rpg.html', 
                               terrain_color=season_data["terrain"], 
                               leaf_color=season_data["leaves"],
                               season_name=season_data["season"],
                               is_christmas=is_christmas,
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
    
    with state_lock:
        state.update({"station": station, "is_sleeping": (station == "bed"), "bubble": bubbles.get(station, "Rerouting..."), "acc_css": "none" if station != "bed" else "zzz", "show_bed": (station == "bed" or now.hour >= 21 or now.hour < 6)})
    
    # Save state locally so the web UI updates instantly across all connected TVs
    try:
        with open(STATE_FILE, 'w') as sf: json.dump(state, sf)
    except: pass
    
    return jsonify(success=True)

# When run with Gunicorn, this starts the sync loop without blocking the workers
if __name__ != '__main__':
    threading.Thread(target=sync_loop, daemon=True).start()
    threading.Thread(target=monitor_loop, daemon=True).start()

if __name__ == '__main__':
    threading.Thread(target=sync_loop, daemon=True).start()
    threading.Thread(target=monitor_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, debug=False)
