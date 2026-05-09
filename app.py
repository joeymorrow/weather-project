from dotenv import load_dotenv
load_dotenv()
import os, requests, threading, time, json, re, sqlite3
import tracemalloc
import fcntl
from contextlib import closing
from flask import Flask, render_template, jsonify, request
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
import google.genai as genai
from google.genai import types
import pytz
from werkzeug.utils import secure_filename

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
UPLOAD_FOLDER = os.path.join("static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
DB_FILE = os.path.join(DATA_DIR, "pulse_history.db")
LOG_DB_FILE = os.path.join(DATA_DIR, "system_logs.db")
manual_override = None
override_expiry = 0
state_lock = threading.Lock()
admin_password = os.environ.get("ADMIN_PASSWORD", "changeme")
DENYLIST = ["profanity", "badword", "controversial", "inappropriate"]

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
    "hourly_list": [],
    "sunrise": "--:-- AM", "sunset": "--:-- PM",
    "weekly_list": [], "weekly_summary": "Analyzing weekly patterns...",
    "emergency": {"active": False, "message": "", "color": "#ff0000"},
    "branding": {"text": "POWERED BY THE CITY OF SAULT STE. MARIE", "color": "#00ffff"},
    "slides": [],
    "managed_theme": "",
    "school_closings": {"sault_closed": False, "other_closings": []}
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

def contains_denied_words(text):
    if not text: return False
    text_lower = text.lower()
    for word in DENYLIST:
        if word in text_lower: return True
    return False

def scrape_closings():
    try:
        from bs4 import BeautifulSoup
        import requests
        res = requests.get("https://www.9and10news.com/school-closings/", timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        closings = []
        sault_closed = False
        eup_schools = [
            "Sault Area", "JKL Bahweting", "Brimley", "DeTour", "Pickford", 
            "Rudyard", "Tahquamenon", "Engadine", "Les Cheneaux", "Moran", "Ojibwe", "Whitefish"
        ]
        for tag in soup.find_all(['tr', 'li', 'p', 'div']):
            text = tag.get_text(" ", strip=True)
            if len(text) > 150: continue
            text_lower = text.lower()
            if "closed" in text_lower or "delay" in text_lower:
                for school in eup_schools:
                    if school.lower() in text_lower:
                        status = "Closed" if "closed" in text_lower else "Delayed"
                        if school == "Sault Area" and status == "Closed":
                            sault_closed = True
                        else:
                            entry = f"{school} {status}"
                            if entry not in closings: closings.append(entry)
        return sault_closed, closings
    except Exception as e:
        print(f"[ERROR] Scrape closings failed: {e}", flush=True)
        return False, []

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
        
        sunrise_dt = datetime.fromtimestamp(sunrise, pytz.utc).astimezone(TZ)
        sunset_dt = datetime.fromtimestamp(sunset, pytz.utc).astimezone(TZ)
        sunrise_str = sunrise_dt.strftime('%I:%M %p').lstrip('0')
        sunset_str = sunset_dt.strftime('%I:%M %p').lstrip('0')
        
        hourly_list = []
        try:
            known = [{"dt": now, "temp": w['main']['temp'], "pop": f['list'][0].get('pop', 0) * 100, "clouds": w.get('clouds', {}).get('all', 0), "desc": w['weather'][0]['main'].lower()}]
            for i in f['list'][:10]:
                dt = datetime.strptime(i['dt_txt'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=pytz.utc).astimezone(TZ)
                known.append({"dt": dt, "temp": i['main']['temp'], "pop": i.get('pop', 0) * 100, "clouds": i['clouds']['all'], "desc": i['weather'][0]['main'].lower()})
            
            start_dt = now.replace(minute=0, second=0, microsecond=0)
            for offset in range(25): # Synthesize 24 continuous hours from current hour!
                target_dt = start_dt + timedelta(hours=offset)
                p1, p2 = known[0], known[-1]
                for k in known:
                    if k["dt"] <= target_dt: p1 = k
                    if k["dt"] >= target_dt:
                        p2 = k
                        break
                
                if p1 == p2:
                    t_temp, t_pop, t_clouds = p1["temp"], p1["pop"], p1["clouds"]
                else:
                    total_sec = (p2["dt"] - p1["dt"]).total_seconds()
                    elapsed_sec = (target_dt - p1["dt"]).total_seconds()
                    ratio = elapsed_sec / total_sec if total_sec > 0 else 0
                    t_temp = p1["temp"] + (p2["temp"] - p1["temp"]) * ratio
                    t_pop = p1["pop"] + (p2["pop"] - p1["pop"]) * ratio
                    t_clouds = p1["clouds"] + (p2["clouds"] - p1["clouds"]) * ratio
                
                hourly_list.append({"time": target_dt.strftime('%I%p').lstrip('0').lower(), "temp": int(round(t_temp)), "pop": int(round(t_pop)), "clouds": int(round(t_clouds)), "desc": p1["desc"]})
        except Exception as e:
            print(f"[ERROR] Hourly parsing: {e}", flush=True)
        
        now = datetime.now(TZ)
        sault_closed = False
        other_closings = []
        if now.month in [10, 11, 12, 1, 2, 3, 4]:
            sault_closed, other_closings = scrape_closings()

        # Calculate tomorrow's forecast (Skipping today's remaining blocks)
        now_str = now.strftime('%Y-%m-%d')
        t_items = [i for i in f['list'] if i['dt_txt'].split(' ')[0] != now_str]
        t_high = int(max([i['main']['temp_max'] for i in t_items[:8]])) if t_items else 0
        t_low = int(min([i['main']['temp_min'] for i in t_items[:8]])) if t_items else 0
        t_desc = t_items[min(4, len(t_items)-1)]['weather'][0]['description'].title() if t_items else "..."
        t_pop = int(max([i.get('pop', 0) for i in t_items[:8]]) * 100) if t_items else 0
        
        # Calculate today's high/low accurately instead of overlapping with tomorrow
        today_items = [i for i in f['list'] if i['dt_txt'].split(' ')[0] == now_str]
        if today_items:
            today_high = int(max(w['main']['temp_max'], max([i['main']['temp_max'] for i in today_items])))
            today_low = int(min(w['main']['temp_min'], min([i['main']['temp_min'] for i in today_items])))
        else:
            today_high = int(w['main']['temp_max'])
            today_low = int(w['main']['temp_min'])
        
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

        weather_desc = w['weather'][0]['description'].lower()
        severe_keywords = ["storm", "tornado", "hurricane", "flood", "thunder", "extreme", "blizzard"]
        is_severe = any(kw in weather_desc for kw in severe_keywords)
        mood_instruction = "IMPORTANT: The current weather is SEVERE. Keep Buddy's tone serious, urgent, and focused on safety. Do not be overly cheerful." if is_severe else "Buddy should be his usual helpful, friendly self."

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
        
        with state_lock:
            last_pulse_topic = state.get("pulse", "")
        
        pulse_task = "Task 2 (Pulse): Adopt the persona of a comforting, poetic night-owl. Provide a quiet 1-2 sentence late-night observation of Sault Ste. Marie's nocturnal rhythm—such as freighters passing through the dark <i>Soo Locks</i>, the ambient glow of the <i>International Bridge</i>, stargazing (if the weather is clear), or the city peacefully resting. Seamlessly weave a brief mention of tomorrow's weather into this comforting thought. Keep the tone warm, hushed, and safe (never eerie or lonely). Wrap specific local landmarks in <i> tags. DO NOT state the current time. Do not search for news. Set 'is_news' to false." if is_late_night else "Task 2 (Pulse): Adopt the persona of a masterful, steadfast speechwriter. You believe deeply in the indomitable human spirit and the rugged, enduring nature of the Upper Peninsula. Provide a concise, profound 2-sentence pulse reflecting the region's true rhythm—such as a recent local milestone, a community gathering, or an ongoing event. Weave the current weather seamlessly into the event description itself—do not append disconnected, dramatic sentences about the weather. Only use stoic or poetic language when it directly anchors to the tangible event you are sharing. Avoid overly cheery, flowery, or saccharine language (e.g., do NOT use words like 'velvet embrace', 'sanctuary', or 'nourishing'). Do not be dramatic or melancholy; be unconquerable and grounded. DO NOT state the current time. If mentioning a scheduled event, explicitly provide both the start and end times (e.g., 'from <i>9:00 AM to 1:00 PM</i>') so locals know exactly when it happens and avoid FOMO. Example of the VIBE: 'Despite the steady rain sweeping off the river, the hum of engines continues at the <i>Farmers Market</i> from <i>8:00 AM to 1:00 PM</i>, proving once again the steadfast spirit of the Soo.' Wrap all specific locations, subjects, and event times in <i> tags. If an event has a fee, append '($)'. Ground the update in vivid, factual UP sensory details. Set 'is_news' to true if sharing a tangible local fact."

        prompt = f"""
        Sault MI. Date: {date_str}. Time: {time_str}. Weather: {w['weather'][0]['description']}. Precip Chance: {pop}%. Forecast: {forecast_context}. Station: {st_id}. Sleep: {is_sleep}.
        PREVIOUS PULSE: "{last_pulse_topic}" -> Provide a completely different topic/event.
        {buddy_task}
        {pulse_task}
        {mood_instruction}
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
                
                if contains_denied_words(new_pulse) or contains_denied_words(ai.get("bubble", "")):
                    new_pulse = "Safe Mode: Standard rhythm today."
                    ai["bubble"] = "Operating in safe mode."
                    ai["suggestion"] = "Stay safe."
                
                if is_news:
                    hist = load_history()
                    # Smart Local Deduplication: Extract data entities using the <i> tags
                    new_tags = set(t.lower() for t in re.findall(r'<i>(.*?)</i>', new_pulse, re.IGNORECASE))
                    is_duplicate_data = False
                    
                    if new_tags:
                        for past in hist[:5]: # Compare against the 5 most recent archived events
                            past_tags = set(t.lower() for t in re.findall(r'<i>(.*?)</i>', past["text"], re.IGNORECASE))
                            if past_tags and len(new_tags.intersection(past_tags)) >= max(1, len(new_tags) // 2):
                                is_duplicate_data = True
                                break
                                
                    if not is_duplicate_data:
                        try:
                            with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                                with conn:
                                    conn.execute("INSERT INTO pulses (date, text) VALUES (?, ?)", (date_str, new_pulse))
                        except sqlite3.IntegrityError:
                            pass # Ignore exact duplicate string matches
                
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
                "temp": int(w['main']['temp']), "high": today_high, 
                "low": today_low,
                "desc": w['weather'][0]['description'].title(), "icon": w['weather'][0]['icon'],
                "date": now.strftime(f"%A, %B {day}{suffix}, %Y"), "time": now.strftime('%I:%M %p'), 
                "station": st_id, "is_sleeping": is_sleep, "show_bed": (st_id == "bed" or h >= 21 or h < 6), 
                "t_high": t_high, "t_low": t_low, "t_desc": t_desc, "t_pop": t_pop,
                "is_late_night": is_late_night,
                "is_day": is_day, "is_golden": is_golden, "pop": pop,
                "hourly_list": hourly_list,
                "sunrise": sunrise_str, "sunset": sunset_str,
                "weekly_list": weekly_list,
                "school_closings": {"sault_closed": sault_closed, "other_closings": other_closings}
            })
            with open(STATE_FILE, 'w') as sf: json.dump(state, sf)
    except Exception as e: 
        print(f"[ERROR] {e}", flush=True)
        with state_lock: 
            state.update({
                "bubble": "I'm having trouble seeing the sky right now, but stay safe!",
                "desc": "Data unavailable",
                "temp": "--", "high": "--", "low": "--", "pop": "--",
                "suggestion": "Stay safe.",
                "forecast": "Weather data currently offline.",
                "pulse": "Our connection to the Sault skies is temporarily interrupted."
            })

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

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if request.method == 'POST':
        if request.form.get('password') == admin_password:
            action = request.form.get('action')
            with state_lock:
                if action == 'update_flare':
                    if request.form.get('flare_active') == 'yes':
                        state['emergency'] = {"active": True, "message": request.form.get('message', ''), "color": request.form.get('color', '#ff0000')}
                    else:
                        state['emergency']['active'] = False
                elif action == 'update_branding':
                    state['branding'] = {
                        "text": request.form.get('branding_text', '').strip(),
                        "color": request.form.get('branding_color', '#00ffff')
                    }
                elif action == 'update_theme':
                    state['managed_theme'] = request.form.get('managed_theme', '')
                elif action == 'add_text_slide':
                    slide = {
                        "id": str(int(time.time())),
                        "type": "text",
                        "text": request.form.get('text', ''),
                        "bg_color": request.form.get('bg_color', '#000000'),
                        "text_color": request.form.get('text_color', '#ffffff'),
                        "strobe": request.form.get('strobe') == 'yes',
                        "duration": int(request.form.get('duration', 15)),
                        "start_time": request.form.get('start_time', ''),
                        "end_time": request.form.get('end_time', '')
                    }
                    state.setdefault('slides', []).append(slide)
                elif action == 'add_image_slide':
                    file = request.files.get('image')
                    if file and file.filename:
                        fname = secure_filename(file.filename)
                        fpath = os.path.join(UPLOAD_FOLDER, f"{int(time.time())}_{fname}")
                        file.save(fpath)
                        slide = {"id": str(int(time.time())), "type": "image", "url": "/" + fpath.replace("\\", "/"), "duration": int(request.form.get('duration', 15)), "start_time": request.form.get('start_time', ''), "end_time": request.form.get('end_time', '')}
                        state.setdefault('slides', []).append(slide)
                elif action == 'delete_slide':
                    sid = request.form.get('slide_id')
                    for s in state.get('slides', []):
                        if s.get('id') == sid and s.get('type') == 'image':
                            try:
                                os.remove(os.path.join(app.root_path, s.get('url').lstrip('/')))
                            except: pass
                    state['slides'] = [s for s in state.get('slides', []) if s.get('id') != sid]
                try:
                    with open(STATE_FILE, 'w') as sf: json.dump(state, sf)
                except: pass
            return "Settings Updated. <a href='/admin' style='color:#00ffff;'>Go Back</a>"
        return "Unauthorized", 401
    with state_lock:
        return render_template('admin.html', emergency=state.get('emergency', {}), branding=state.get('branding', {}), slides=state.get('slides', []), managed_theme=state.get('managed_theme', ''))
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
