from dotenv import load_dotenv
load_dotenv()
import os, requests, threading, time, json, re, sqlite3
import tracemalloc
import fcntl
from contextlib import closing
from flask import Flask, render_template, jsonify, request
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
import google.genai as genai
from google.genai import types
import pytz

def send_alert_email(subject, body):
    try:
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = os.environ.get("SMTP_USER", "buddy-alerts@morrowedge.com")
        msg['To'] = "joseph@morrowedge.com"
        
        smtp_server = os.environ.get("SMTP_SERVER", "localhost")
        smtp_port = int(os.environ.get("SMTP_PORT", 587))
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            if os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASS"):
                server.starttls()
                server.login(os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASS"))
            server.send_message(msg)
        log_system_event("EMAIL_SENT", f"Sent alert: {subject}")
    except Exception as e:
        print(f"[ERROR] Failed to send email: {e}", flush=True)

client_alerts = {"last_sent": 0}

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
    "is_day": False, "is_golden": False, "pop": 0, "pulse_history": load_history(),
    "weekly_list": [], "weekly_summary": "Analyzing weekly patterns..."
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
        clouds = w.get('clouds', {}).get('all', 0)
        
        is_morning_golden = (sunrise - 900 <= now_ts < sunrise + 2700)
        is_evening_golden = (sunset - 2700 <= now_ts < sunset + 900)
        is_golden = (is_morning_golden or is_evening_golden) and (clouds < 75)
        is_day = (sunrise <= now_ts < sunset) and not is_golden
        pop = int(f['list'][0].get('pop', 0) * 100)
        
        # Calculate tomorrow's forecast (Skipping today's remaining blocks)
        now_str = now.strftime('%Y-%m-%d')
        t_items = [i for i in f['list'] if i['dt_txt'].split(' ')[0] != now_str]
        t_high = int(max([i['main']['temp_max'] for i in t_items[:8]])) if t_items else 0
        t_low = int(min([i['main']['temp_min'] for i in t_items[:8]])) if t_items else 0
        t_desc = t_items[min(4, len(t_items)-1)]['weather'][0]['description'].title() if t_items else "..."
        t_pop = int(max([i.get('pop', 0) for i in t_items[:8]]) * 100) if t_items else 0
        
        # Calculate 5-Day Outlook
        daily_forecasts = {}
        for item in f['list']:
            d_str = item['dt_txt'].split(' ')[0]
            if d_str not in daily_forecasts:
                daily_forecasts[d_str] = {'high': -100, 'low': 100, 'icons': []}
            daily_forecasts[d_str]['high'] = max(daily_forecasts[d_str]['high'], item['main']['temp_max'])
            daily_forecasts[d_str]['low'] = min(daily_forecasts[d_str]['low'], item['main']['temp_min'])
            daily_forecasts[d_str]['icons'].append(item['weather'][0]['icon'].replace('n', 'd'))
            
        weekly_list = []
        for dt, dat in list(daily_forecasts.items())[1:6]:
            d_icon = max(set(dat['icons']), key=dat['icons'].count) if dat['icons'] else "01d"
            day_name = datetime.strptime(dt, '%Y-%m-%d').strftime('%a')
            weekly_list.append({"day": day_name, "high": int(dat['high']), "low": int(dat['low']), "icon": d_icon})
            
        is_late_night = (now.hour == 21 and now.minute >= 30) or (now.hour >= 22)

        buddy_task = "Task 1 (Buddy): 3-5 word unique greeting observing the beautiful sunrise." if (is_morning_golden and clouds < 75) else "Task 1 (Buddy): 3-5 word technical activity (Passat maintenance, lab coding)."

        global manual_override
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
        
        pulse_task = "Task 2 (Pulse): 1-sentence sleek summary of tomorrow's weather forecast. Start with 'Tomorrow:'. Make it conversational, descriptive, and highly engaging. Do not search for news. Set 'is_news' to false." if is_late_night else "Task 2 (Pulse): Adopt the persona of a masterful, charismatic writer (akin to a top-tier presidential speechwriter). You wake up aware of the world's worries, but your mission is to ease them. You believe deeply in the indomitable human spirit, the comforting cyclical patterns of life, and that providing this daily pulse is a small effort to lift the city's spirits. Provide a concise, profound 2-sentence pulse on the region's true rhythm that resonates with all ages. Be robust and grounded—do not make 'charisma' your entire personality; let the real world do the talking. STRICT RULE: Rely ONLY on the provided weather/date context. Never hallucinate seasonal details (e.g., do not mention snow or ice unless the current weather data confirms it is freezing). Search the web for real local happenings in Sault Ste. Marie or the EUP. Examples of the VIBE (do not copy literally unless true): 'A full flight is landing at CIU tonight...', 'Sherman Park Beach will be packed today.', 'The Kincheloe garage sale starts Friday.', or 'Take a quiet drive to Mackinac City!'. Ground the update in vivid, factual UP sensory details (e.g., the hum of the highway, or actual weather). Refer to the city as 'the Sault' or 'the Soo'. If your update shares a tangible local fact, event, or specific community detail, set 'is_news' to true. Otherwise, set it to false."

        prompt = f"""
        Sault MI. Date: {date_str}. Time: {time_str}. Weather: {w['weather'][0]['description']}. Precip Chance: {pop}%. Forecast: {forecast_context}. Station: {st_id}. Sleep: {is_sleep}.
        {buddy_task}
        {pulse_task}
        Task 3 (Forecast): 1 short sentence summarizing today/tomorrow's weather based on forecast.
        Task 4 (Attire): 2-4 word practical clothing/gear suggestion based on the forecast. Factor in current season ({now.strftime('%B')}).
        Task 5 (Weekly): 1-2 sentence overall outlook for the upcoming 5 days based on the forecast trend.
        Return JSON: {{ "tip": "attire", "say": "task", "pulse": "vibe", "acc": "tool/none", "forecast": "summary", "weekly_summary": "outlook", "is_news": true }}
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
                        "weekly_summary": ai.get("weekly_summary", "Weekly pattern steady."),
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
                "t_high": t_high, "t_low": t_low, "t_desc": t_desc, "t_pop": t_pop,
                "is_late_night": is_late_night,
                "is_day": is_day, "is_golden": is_golden, "pop": pop,
                "weekly_list": weekly_list
            })
            with open(STATE_FILE, 'w') as sf: json.dump(state, sf)
    except Exception as e: 
        print(f"[ERROR] {e}", flush=True)
        with state_lock: state["bubble"] = "Signal degraded. Retrying..."

def sync_loop():
    # Prevent multiple Gunicorn workers from spawning redundant background threads!
    lock_file = open(os.path.join(DATA_DIR, "sync_loop.lock"), "a")
    try:
        fcntl.lockf(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        return # Another worker is already running the sync loop. Exit quietly.

    while True:
        run_sync()
        time.sleep(600)

def monitor_loop():
    # Prevent multiple Gunicorn workers from spawning redundant monitor threads
    lock_file = open(os.path.join(DATA_DIR, "monitor_loop.lock"), "a")
    try:
        fcntl.lockf(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        return # Another worker is already running the monitor loop.

    tracemalloc.start()
    baseline_snapshot = tracemalloc.take_snapshot()
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
        top_stats = current_snapshot.compare_to(baseline_snapshot, 'lineno')
        total_growth = sum(stat.size_diff for stat in top_stats)
        if total_growth > 1024 * 1024 * 5: # Log if memory grew by more than 5MB since baseline
            leak_details = [str(stat) for stat in top_stats[:5]]
            log_system_event("MEMORY_GROWTH", f"Memory grew by {total_growth / 1024:.1f} KiB since baseline.", {"top_5_leaks": leak_details})
            
            # Heuristic 1: Deduplication (Ignore if it's the exact same leak signature)
            current_sig = "".join([str(stat.traceback) for stat in top_stats[:3]])
            if current_sig == last_leak_signature:
                continue

            # Heuristic 2: Backoff (Max 1 alert every 12 hours)
            current_time = time.time()
            if current_time - last_leak_email_time < 43200:
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
                            send_alert_email("[CRITICAL - BEACON BUDDY] - Memory Leak", f"Reason: {ai_eval.get('reason')}\n\nPaste this into gemini to find the solution: \n{leak_details}\n\nPrompt Suggestion: \n{ai_eval.get('prompt_suggestion')} \n\n(Geared for your specific chat history context!)")
                            last_leak_email_time = current_time
                            last_leak_signature = current_sig
                            baseline_snapshot = current_snapshot # Reset baseline ONLY after alerting!
                        break # AI evaluated successfully, break fallback loop
                    except: continue
            except Exception as e:
                print(f"[ERROR] AI Leak eval/email failed: {e}", flush=True)

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

@app.route('/api/telemetry/report', methods=['POST'])
def telemetry_report():
    data = request.json
    if not data: return jsonify(success=False), 400

    issue_type = data.get("type", "UNKNOWN")
    metric = data.get("metric", 0)
    url = data.get("url", "unknown")
    extra = data.get("extra", {})

    log_system_event("CLIENT_ISSUE", f"Client reported {issue_type} on {url}", {"metric": metric, "extra": extra})

    current_time = time.time()
    if current_time - client_alerts["last_sent"] > 43200: # 12 hours global cooldown to strictly prevent spam!
        if issue_type == "HIGH_MEMORY":
            extra_str = json.dumps(extra, indent=2) if extra else "No extra diagnostics."
            send_alert_email(
                subject=f"[WARNING - BEACON BUDDY] - Client Memory Leak on {url}",
                body=f"A user's browser reported massive memory usage.\n\nURL: {url}\nReported JS Heap: {metric} MB.\n\nDiagnostic Data:\n{extra_str}\n\nThis indicates a client-side memory leak. To prevent spam, this alert will not trigger again for 12 hours."
            )
            client_alerts["last_sent"] = current_time
    return jsonify(success=True)

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
    global manual_override, override_expiry
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
