from dotenv import load_dotenv
load_dotenv()
import os, requests, threading, time, json, re, sqlite3
import tracemalloc
import filelock
import psutil
import copy
from contextlib import closing
from flask import Flask, render_template, jsonify, request, redirect, send_from_directory, session, url_for
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
import google.genai as genai
from google.genai import types
import socket, struct, select
import pytz
from werkzeug.utils import secure_filename
import markdown
import msal

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

http_session = requests.Session()

app = Flask(__name__)
TZ = pytz.timezone('America/Detroit')
G_KEY = os.environ.get("GEMINI_API_KEY", "")
OWM_KEY = os.environ.get("OPENWEATHERMAP_API_KEY", "")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

if os.environ.get("HOST_DATA_DIR") and not os.path.exists("/.dockerenv"):
    DATA_DIR = os.environ.get("HOST_DATA_DIR")

os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.path.join(DATA_DIR, "buddy_state.json")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
DB_FILE = os.path.join(DATA_DIR, "pulse_history.db")
LOG_DB_FILE = os.path.join(DATA_DIR, "system_logs.db")
manual_override = None
override_expiry = 0
state_lock = threading.Lock()
slide_history = {'undo': [], 'redo': []}
admin_username = os.environ.get("ADMIN_USERNAME", "admin")
admin_password = os.environ.get("ADMIN_PASSWORD", "changeme")
INTERNAL_API_SECRET = os.environ.get("INTERNAL_API_SECRET")
DENYLIST = ["profanity", "badword", "controversial", "inappropriate"]

app.secret_key = INTERNAL_API_SECRET or os.urandom(24)

def init_db():
    with closing(sqlite3.connect(DB_FILE, timeout=10)) as pulse_conn:
        with pulse_conn:
            pulse_conn.execute('''CREATE TABLE IF NOT EXISTS pulses (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                date TEXT,
                                text TEXT UNIQUE
                             )''')
            pulse_conn.execute('''CREATE TABLE IF NOT EXISTS beacon_pages (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                slug TEXT UNIQUE,
                                title TEXT,
                                zipcode TEXT
                             )''')
            pulse_conn.execute('''CREATE TABLE IF NOT EXISTS hallucinations_log (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                retrieved_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                                date TEXT,
                                text TEXT,
                                reason TEXT
                             )''')
            pulse_conn.execute('''CREATE TABLE IF NOT EXISTS old_pulses (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                archived_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                                date TEXT,
                                text TEXT
                             )''')
            pulse_conn.execute('''CREATE TABLE IF NOT EXISTS eap_subscriptions (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                multicast_ip TEXT,
                                port INTEGER,
                                profile TEXT
                             )''')
            pulse_conn.execute('''CREATE TABLE IF NOT EXISTS sso_configs (
                                provider TEXT PRIMARY KEY,
                                enabled BOOLEAN,
                                client_id TEXT,
                                client_secret TEXT,
                                extra_info TEXT
                             )''')
            pulse_conn.execute('''CREATE TABLE IF NOT EXISTS rbac_users (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                username TEXT UNIQUE,
                                role TEXT,
                                provider TEXT,
                                type TEXT DEFAULT 'User',
                                override_group BOOLEAN DEFAULT 0
                             )''')
            try:
                pulse_conn.execute("ALTER TABLE rbac_users ADD COLUMN type TEXT DEFAULT 'User'")
                pulse_conn.execute("ALTER TABLE rbac_users ADD COLUMN override_group BOOLEAN DEFAULT 0")
            except sqlite3.OperationalError:
                pass
    with closing(sqlite3.connect(LOG_DB_FILE, timeout=10)) as log_conn:
        with log_conn:
            log_conn.execute('''CREATE TABLE IF NOT EXISTS logs (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                                log_type TEXT,
                                message TEXT,
                                details TEXT
                             )''')
            log_conn.execute('''CREATE TABLE IF NOT EXISTS metrics (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                                load_avg REAL,
                                mem_used_mb REAL,
                                cache_mb REAL
                             )''')
            log_conn.execute('''CREATE TABLE IF NOT EXISTS audit_logs (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                                user TEXT,
                                action TEXT,
                                details TEXT
                             )''')
init_db()

def get_agenda_item_count():
    try:
        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM pulses")
            return c.fetchone()[0]
    except Exception as e:
        print(f"[ERROR] get_agenda_item_count: {e}", flush=True)
        return 0

@app.context_processor
def inject_agenda_count():
    return dict(agenda_item_count=get_agenda_item_count())

def get_beacon_pages():
    try:
        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
            c = conn.cursor()
            c.execute("SELECT id, slug, title, zipcode FROM beacon_pages ORDER BY title ASC")
            return [{"id": r[0], "slug": r[1], "title": r[2], "zipcode": r[3]} for r in c.fetchall()]
    except Exception as e:
        print(f"[ERROR] get_beacon_pages: {e}", flush=True)
        return []

def get_eap_subscriptions():
    try:
        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
            c = conn.cursor()
            c.execute("SELECT id, multicast_ip, port, profile FROM eap_subscriptions")
            return [{"id": r[0], "ip": r[1], "port": r[2], "profile": r[3]} for r in c.fetchall()]
    except:
        return []

def load_history(today_str=None, yesterday_str=None):
    try:
        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
            c = conn.cursor()
            if today_str and yesterday_str:
                c.execute("""
                    SELECT date, text FROM pulses 
                    WHERE date = ? OR date = ? OR id IN (
                        SELECT id FROM pulses ORDER BY id DESC LIMIT 21
                    )
                    ORDER BY id DESC
                """, (today_str, yesterday_str))
            else:
                c.execute("SELECT date, text FROM pulses ORDER BY id DESC LIMIT 21")
            return [{"date": r[0], "text": r[1]} for r in c.fetchall()]
    except Exception as e:
        print(f"[ERROR] load_history: {e}", flush=True)
        return []

def log_system_event(log_type, message, details=""):
    try:
        with closing(sqlite3.connect(LOG_DB_FILE, timeout=10)) as conn:
            with conn:
                conn.execute("INSERT INTO logs (log_type, message, details) VALUES (?, ?, ?)",
                             (log_type, message, json.dumps(details) if isinstance(details, dict) else str(details)))
    except Exception as e:
        print(f"[ERROR] Failed to write to system log: {e}", flush=True)

def log_audit_event(user, action, details=""):
    try:
        with closing(sqlite3.connect(LOG_DB_FILE, timeout=10)) as conn:
            with conn:
                conn.execute("INSERT INTO audit_logs (user, action, details) VALUES (?, ?, ?)",
                             (user, action, json.dumps(details) if isinstance(details, dict) else str(details)))
    except Exception as e:
        print(f"[ERROR] Failed to write to audit log: {e}", flush=True)

state = {
    "temp": 0, "suggestion": "Initializing...", "station": "office", 
    "desc": "Syncing...", "high": 0, "low": 0, "date": "", "time": "--", "icon": "01d",
    "bubble": "...", "pulse": "Anchoring Sault Pulse...",
    "forecast": "Loading forecast...", "acc_css": "none", "is_sleeping": False, "show_bed": False,
    "is_day": False, "is_golden": False, "pop": 0, "pulse_history": load_history(),
    "hourly_list": [],
    "sunrise": "--:-- AM", "sunset": "--:-- PM",
    "clouds": 0, "humidity": 0, "wind": "0 mph N", "uv_index": "0 (low)",
    "weekly_list": [], "weekly_summary": "Analyzing weekly patterns...",
    "emergency": {"active": False, "message": "", "color": "#ff0000"},
    "branding": {"text": "", "color": "#00ffff"},
    "main_config": {"header": "MORROW EDGE | BEACON Buddy", "location": "SAULT STE. MARIE, MICHIGAN", "query": "Sault+Ste.+Marie,MI,US"},
    "slides": [],
    "managed_theme": "",
    "school_closings": {"sault_closed": False, "other_closings": []},
    "agenda_item_count": 0,
    "school_alerts": {},
    "agenda_votes": {},
    "disabled_pages": []
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

def sync_for_location(slug, loc_name, query):
    try:
        w = http_session.get(f"https://api.openweathermap.org/data/2.5/weather?q={query}&appid={OWM_KEY}&units=imperial", timeout=10).json()
        f = http_session.get(f"https://api.openweathermap.org/data/2.5/forecast?q={query}&appid={OWM_KEY}&units=imperial", timeout=10).json()
        now = datetime.now(TZ)
        h = now.hour
        is_sleep = (h >= 22 or h < 6)
        st_id = "bed" if is_sleep else next((v for k,v in {21:"kitchen", 20:"garage", 19:"library", 17:"store", 16:"gym", 8:"office", 6:"coffee"}.items() if h >= k), "coffee")
        
        # Calculate dynamic daylight state and precipitation
        raw_sunrise = w['sys']['sunrise']
        raw_sunset = w['sys']['sunset']
        now_ts = now.timestamp()
        clouds = w.get('clouds', {}).get('all', 0)
        
        # Force sunrise and sunset to the current local day to prevent OpenWeatherMap rollover bugs
        sunrise_dt = datetime.fromtimestamp(raw_sunrise, pytz.utc).astimezone(TZ).replace(year=now.year, month=now.month, day=now.day)
        sunset_dt = datetime.fromtimestamp(raw_sunset, pytz.utc).astimezone(TZ).replace(year=now.year, month=now.month, day=now.day)
        
        sunrise = sunrise_dt.timestamp()
        sunset = sunset_dt.timestamp()
        
        is_morning_golden = (sunrise - 900 <= now_ts < sunrise + 2700)
        is_evening_golden = (sunset - 2700 <= now_ts < sunset + 900)
        is_golden = (is_morning_golden or is_evening_golden) and (clouds < 75)
        is_day = (sunrise <= now_ts < sunset) and not is_golden
        pop = int(f['list'][0].get('pop', 0) * 100)
        
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
        logical_now_str = (now - timedelta(hours=6)).strftime('%Y-%m-%d')
        t_items = [i for i in f['list'] if i['dt_txt'].split(' ')[0] != logical_now_str]
        t_high = int(max([i['main']['temp_max'] for i in t_items[:8]])) if t_items else 0
        t_low = int(min([i['main']['temp_min'] for i in t_items[:8]])) if t_items else 0
        t_desc = t_items[min(4, len(t_items)-1)]['weather'][0]['description'].title() if t_items else "..."
        t_pop = int(max([i.get('pop', 0) for i in t_items[:8]]) * 100) if t_items else 0
        
        clouds_val = w.get('clouds', {}).get('all', 0)
        humidity_val = w.get('main', {}).get('humidity', 0)
        wind_val = w.get('wind', {}).get('speed', 0)
        wind_deg = w.get('wind', {}).get('deg', 0)
        dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
        wind_dir = dirs[int((wind_deg / 22.5) + 0.5) % 16]
        wind_str = f"{wind_val} mph {wind_dir}"
        uv_val = 0 if not is_day else max(1, int(10 - (clouds_val / 10)))
        uv_str = f"{uv_val} (low)" if uv_val < 3 else (f"{uv_val} (moderate)" if uv_val < 6 else f"{uv_val} (high)")
        
        # Calculate today's high/low accurately instead of overlapping with tomorrow
        today_items = [i for i in f['list'] if i['dt_txt'].split(' ')[0] == logical_now_str]
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
            date_short = datetime.strptime(dt, '%Y-%m-%d').strftime('%m/%d')
            weekly_list.append({"day": day_name, "date_short": date_short, "high": int(dat['high']), "low": int(dat['low']), "icon": d_icon})
            
        is_post_midnight = now.hour < 6
        is_late_night = (now.hour == 21 and now.minute >= 30) or (now.hour >= 22) or is_post_midnight
        tomorrow_label = "later today" if is_post_midnight else "tomorrow"
        tomorrow_ui_label = "Later Today" if is_post_midnight else "Tomorrow"

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
            if slug == "main": # Only reset manual override once per loop
                manual_override = None

        global gemini_client
        if not gemini_client: gemini_client = genai.Client(api_key=G_KEY)
        forecast_context = ", ".join([f"{i['dt_txt'].split(' ')[1][:5]} {i['weather'][0]['description']} {int(i['main']['temp'])}F" for i in f['list'][:8]])
        time_str = now.strftime('%I:%M %p')
        date_str = now.strftime('%B %d')
        
        with state_lock:
            if slug == "main":
                last_pulse_topic = state.get("pulse", "")
            else:
                last_pulse_topic = state.get("tenants", {}).get(slug, {}).get("pulse", "")
        
        pulse_task = (
            f"Task 2 (Pulse): Adopt the persona of a comforting, poetic night-owl. Provide a quiet 1-2 sentence late-night observation of {loc_name}'s nocturnal rhythm. Seamlessly weave a brief mention of {tomorrow_label}'s weather into this comforting thought. Keep the tone warm, hushed, and safe (never eerie or lonely). Wrap specific local landmarks in <i> tags. DO NOT state the current time. Do not search for news. Set 'is_news' to false." 
            if is_late_night else 
            f"Task 2 (Pulse): Adopt the persona of an inspiring, steadfast community leader and local speechwriter. "
            f"ANTI-HALLUCINATION PROTOCOL (SAC): 1. SEARCH for recent local news, community successes, acts of kindness, or verifiable events happening TODAY in {loc_name}. 2. If no specific news or event is found, DO NOT invent names or fake heroics; instead, offer a grounded note of gratitude for the community's resilience or a 'Seasonal Rhythm' (e.g., <i>shipping traffic</i>). 3. CONTENT: Provide a 2-sentence update. Give a brief shoutout/kudos to a local achievement OR share the real event, weaving the current weather into this message seamlessly. Make the reader feel proud and ready to take on the day. 4. TRUTH BOUNDARY: Only include specific details if verified. 5. FORMATTING: Wrap specific locations, subjects, and verified event times in <i> tags. Avoid overly saccharine words. Set 'is_news' to true ONLY if specific verified news/events are shared; otherwise, set to false."
        )

        prompt = f"""
        {loc_name}. Date: {date_str}. Time: {time_str}. Weather: {w['weather'][0]['description']}. Precip Chance: {pop}%. Forecast: {forecast_context}. Station: {st_id}. Sleep: {is_sleep}.
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
        new_pulse = "Anchoring Sault Pulse..." if slug == "main" else f"Anchoring {loc_name} Pulse..."
        is_news = False
        ai = {}

        for m_id in get_best_models():
            try:
                resp = gemini_client.models.generate_content(
                    model=m_id, 
                    contents=prompt,
                    config=types.GenerateContentConfig(tools=[{"google_search": {}}])
                )
                text = resp.text
                json_start = text.find('{')
                json_end = text.rfind('}')
                if json_start == -1 or json_end == -1:
                    raise ValueError("Invalid JSON boundaries from AI")
                json_str = text[json_start:json_end+1]
                ai = json.loads(json_str)
                
                new_pulse = ai.get("pulse", new_pulse)
                is_news = str(ai.get("is_news", False)).lower() in ["true", "1", "yes"]
                
                if is_news and new_pulse and "Anchoring" not in new_pulse:
                    try:
                        check_prompt = f"""
You are a strict fact-checker evaluating a newly generated "Pulse" event for {loc_name}. Use Google Search to verify if the event actually happened or is scheduled around the time it was reported before making a judgment.
Determine if this text is a hallucinated/invented specific event (like fake workshops or generic community gatherings with fake times), or if it is legitimate.
Text: "{new_pulse}"
Return ONLY valid JSON: {{"hallucinated": true/false}}
"""
                        for m_check in get_best_models():
                            try:
                                check_resp = gemini_client.models.generate_content(
                                    model=m_check, 
                                    contents=check_prompt,
                                    config=types.GenerateContentConfig(tools=[{"google_search": {}}])
                                )
                                check_text = check_resp.text
                                c_start = check_text.find('{')
                                c_end = check_text.rfind('}')
                                if c_start == -1 or c_end == -1: raise ValueError("No JSON in check")
                                check_data = json.loads(check_text[c_start:c_end+1])
                                
                                if check_data.get("hallucinated"):
                                    print(f"[API] Hallucination caught in-flight for {slug}! Replacing with fallback.", flush=True)
                                    import random
                                    with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                                        c = conn.cursor()
                                        c.execute("SELECT text FROM pulses ORDER BY id DESC LIMIT 18")
                                        recent = [r[0] for r in c.fetchall()]
                                        new_pulse = random.choice(recent) if recent and slug == "main" else f"{loc_name} continues its steady rhythm."
                                    is_news = False
                                break
                            except Exception:
                                continue
                    except Exception as e:
                        pass

                if contains_denied_words(new_pulse) or contains_denied_words(ai.get("bubble", "")):
                    new_pulse = "Safe Mode: Standard rhythm today."
                    ai["bubble"] = "Operating in safe mode."
                    ai["suggestion"] = "Stay safe."
                
                yesterday_str = (now - timedelta(days=1)).strftime('%B %d')
                if is_news and slug == "main": # Only log main pulses to global history for deduplication
                    hist = load_history(date_str, yesterday_str)
                    new_tags = set(t.lower() for t in re.findall(r'<i>(.*?)</i>', new_pulse, re.IGNORECASE))
                    is_duplicate_data = False
                    if new_tags:
                        for past in hist[:5]:
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
                            pass
                
                print(f"[API] Success via {m_id} for {slug}", flush=True)
                success = True; break
            except: continue
        
        if not success: ai["bubble"] = "Optimizing antenna..."

        day = now.day
        suffix = 'th' if 11 <= day <= 13 else {1:'st', 2:'nd', 3:'rd'}.get(day % 10, 'th')
        
        hist = load_history(date_str, (now - timedelta(days=1)).strftime('%B %d')) if slug == "main" else []

        update_dict = {
                "temp": int(w['main']['temp']), "high": today_high, 
                "low": today_low,
                "desc": w['weather'][0]['description'].title(), "icon": w['weather'][0]['icon'],
                "date": now.strftime(f"%A, %B {day}{suffix}, %Y"), "time": now.strftime('%I:%M %p'), 
                "station": st_id, "is_sleeping": is_sleep, "show_bed": (st_id == "bed" or now.hour >= 21 or now.hour < 6), 
                "t_high": t_high, "t_low": t_low, "t_desc": t_desc, "t_pop": t_pop,
                "tomorrow_label": tomorrow_ui_label,
                "is_late_night": is_late_night,
                "is_day": is_day, "is_golden": is_golden, "pop": pop,
                "clouds": clouds_val, "humidity": humidity_val,
                "wind": wind_str, "uv_index": uv_str,
                "hourly_list": hourly_list,
                "sunrise": sunrise_str, "sunset": sunset_str,
                "weekly_list": weekly_list,
                "suggestion": ai.get("tip"), "bubble": ai.get("say", ai.get("bubble", "...")), 
                "pulse": new_pulse, "acc_css": "zzz" if is_sleep else ai.get("acc", "none"),
                "forecast": ai.get("forecast", "Weather data processing..."), 
                "weekly_summary": ai.get("weekly_summary", "Weekly pattern steady."),
                "pulse_history": hist,
                "school_closings": {"sault_closed": sault_closed, "other_closings": other_closings}
        }

        with state_lock:
            if slug == "main":
                state.update(update_dict)
            else:
                if 'tenants' not in state: state['tenants'] = {}
                if slug not in state['tenants']: state['tenants'][slug] = {}
                state['tenants'][slug].update(update_dict)
            try:
                with open(STATE_FILE, 'w') as sf: json.dump(state, sf)
            except: pass

    except Exception as e: 
        print(f"[ERROR] sync_for_location {slug}: {e}", flush=True)
        with state_lock: 
            err_dict = {
                "bubble": "I'm having trouble seeing the sky right now, but stay safe!",
                "desc": "Data unavailable",
                "temp": "--", "high": "--", "low": "--", "pop": "--",
                "suggestion": "Stay safe.",
                "forecast": "Weather data currently offline.",
                "pulse": f"Our connection to {loc_name} skies is temporarily interrupted."
            }
            if slug == "main":
                state.update(err_dict)
            else:
                if 'tenants' not in state: state['tenants'] = {}
                if slug not in state['tenants']: state['tenants'][slug] = {}
                state['tenants'][slug].update(err_dict)

def run_sync():
    # Scrape closings once globally
    now = datetime.now(TZ)
    sault_closed = False
    other_closings = []
    if now.month in [10, 11, 12, 1, 2, 3, 4]:
        sault_closed, other_closings = scrape_closings()
        
    with state_lock:
        state['school_closings'] = {"sault_closed": sault_closed, "other_closings": other_closings}
        state['agenda_item_count'] = get_agenda_item_count()

    # Gather all locations to sync
    locations = []
    with state_lock:
        main_loc = state.get("main_config", {}).get("location", "Sault Ste. Marie, Michigan")
        main_query = state.get("main_config", {}).get("query", "Sault+Ste.+Marie,MI,US")
    locations.append({"slug": "main", "location": main_loc, "query": main_query})
    locations.append({"slug": "sault-schools", "location": "Sault Ste. Marie, Michigan", "query": "Sault+Ste.+Marie,MI,US"})
    locations.append({"slug": "pickford-schools", "location": "Pickford, Michigan", "query": "Pickford,MI,US"})
    
    for p in get_beacon_pages():
        locations.append({"slug": p["slug"], "location": p["title"], "query": p["zipcode"]})
        
    for loc in locations:
        with state_lock: # Acquire lock to safely read disabled_pages
            if loc['slug'] in state.get('disabled_pages', []):
                print(f"[SYNC] Skipping disabled page: {loc['slug']}", flush=True)
                continue # Skip calling sync_for_location for disabled pages
        
        sync_for_location(loc['slug'], loc['location'], loc['query'])
        time.sleep(2) # Avoid aggressive rate-limiting from OpenWeather/Gemini

def record_telemetry():
    try:
        load_avg = os.getloadavg()[0] if hasattr(os, 'getloadavg') else psutil.cpu_percent()
        mem = psutil.virtual_memory()
        mem_used = mem.used / (1024 * 1024)
        cached = getattr(mem, 'cached', 0) / (1024 * 1024)
        with closing(sqlite3.connect(LOG_DB_FILE, timeout=10)) as conn:
            with conn:
                conn.execute("INSERT INTO metrics (load_avg, mem_used_mb, cache_mb) VALUES (?, ?, ?)", (load_avg, mem_used, cached))
    except Exception as e:
        print(f"[ERROR] Telemetry: {e}", flush=True)

def sync_loop():
    # Prevent multiple Gunicorn workers from spawning redundant background threads!
    lock = filelock.FileLock(os.path.join(DATA_DIR, "sync_loop.lock"))
    try:
        lock.acquire(timeout=0)
    except filelock.Timeout:
        return # Another worker is already running the sync loop. Exit quietly.

    while True:
        run_sync()
        time.sleep(600)

def monitor_loop():
    # Prevent multiple Gunicorn workers from spawning redundant monitor threads
    lock = filelock.FileLock(os.path.join(DATA_DIR, "monitor_loop.lock"))
    try:
        lock.acquire(timeout=0)
    except filelock.Timeout:
        return # Another worker is already running the monitor loop.

    tracemalloc.start()
    baseline_snapshot = tracemalloc.take_snapshot()
    initial_threads = threading.enumerate()
    log_system_event("MONITOR_START", f"Monitoring started with {len(initial_threads)} threads.", {"threads": [t.name for t in initial_threads]})

    last_leak_email_time = 0
    last_leak_signature = ""

    while True:
        time.sleep(300) # Run every 5 minutes
        
        record_telemetry()

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
        if total_growth > 1024 * 1024 * 20: # Log if memory grew by more than 20MB since baseline
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
                        t_start = resp.text.find('{')
                        t_end = resp.text.rfind('}')
                        if t_start == -1 or t_end == -1: raise ValueError("No JSON in eval")
                        ai_eval = json.loads(resp.text[t_start:t_end+1])
                    
                        if ai_eval.get("critical"):
                            send_alert_email("[CRITICAL - BEACON BUDDY] - Memory Leak", f"Reason: {ai_eval.get('reason')}\n\nPaste this into gemini to find the solution: \n{leak_details}\n\nPrompt Suggestion: \n{ai_eval.get('prompt_suggestion')} \n\n(Geared for your specific chat history context!)")
                            last_leak_email_time = current_time
                            last_leak_signature = current_sig
                            baseline_snapshot = current_snapshot # Reset baseline ONLY after alerting!
                        break # AI evaluated successfully, break fallback loop
                    except: continue
            except Exception as e:
                print(f"[ERROR] AI Leak eval/email failed: {e}", flush=True)

def hallucination_cleanup_loop():
    # Prevent multiple workers from running redundant loops
    lock = filelock.FileLock(os.path.join(DATA_DIR, "cleanup_loop.lock"))
    try:
        lock.acquire(timeout=0)
    except filelock.Timeout:
        return # Another worker is running

    import subprocess
    while True:
        now = datetime.now(TZ)
        next_hour = ((now.hour // 3) + 1) * 3
        if next_hour >= 24:
            next_run = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            next_run = now.replace(hour=next_hour, minute=0, second=0, microsecond=0)
        
        sleep_seconds = (next_run - now).total_seconds()
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
            
        try:
            subprocess.run(["python", os.path.join(BASE_DIR, "cleanup_hallucinations.py"), "--run-auto"], check=False)
        except Exception as e:
            print(f"[ERROR] Hallucination cleanup loop: {e}", flush=True)

def handle_eap_message(msg_text):
    try:
        data = json.loads(msg_text)
        msg_type = data.get("type", "EAP ALERT").upper()
        msg = data.get("message", "Emergency Alert Activated")
    except:
        msg_type = "EAP ALERT"
        msg = msg_text.strip()

    msg_lower = msg.lower()
    color = "#d32f2f" # Default Red (Lockdown/Fire/Evacuate)
    if "secure" in msg_lower or "hazard" in msg_lower or "weather" in msg_lower: color = "#ff8c00"
    elif "hold" in msg_lower: color = "#800080"
    elif "shelter" in msg_lower or "medical" in msg_lower: color = "#1976d2"
    elif "clear" in msg_lower: color = "#388e3c"

    with state_lock:
        if 'school_alerts' not in state: state['school_alerts'] = {}
        tenants = ['sault-schools', 'pickford-schools'] + [p['slug'] for p in get_beacon_pages()]
        for t in tenants:
            state['school_alerts'][t] = {'type': msg_type, 'color': color, 'message': msg}
        try:
            with open(STATE_FILE, 'w') as sf: json.dump(state, sf)
        except: pass

def eap_multicast_listener():
    # Prevent multiple Gunicorn workers from binding to the UDP port
    lock = filelock.FileLock(os.path.join(DATA_DIR, "eap_listener.lock"))
    try:
        lock.acquire(timeout=0)
    except filelock.Timeout:
        return # Another worker is running the listener

    active_sockets = {}
    while True:
        try:
            subs = get_eap_subscriptions()
            current_endpoints = {(s["ip"], s["port"]): s["profile"] for s in subs}
            for ep in list(active_sockets.keys()):
                if ep not in current_endpoints:
                    active_sockets[ep].close(); del active_sockets[ep]
            for ep, profile in current_endpoints.items():
                ip, port = ep
                if ep not in active_sockets:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    sock.bind(('', port))
                    mreq = struct.pack("4sl", socket.inet_aton(ip), socket.INADDR_ANY)
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                    sock.setblocking(False)
                    active_sockets[ep] = sock
            if active_sockets:
                ready_socks, _, _ = select.select(list(active_sockets.values()), [], [], 5.0)
                for sock in ready_socks:
                    data, _ = sock.recvfrom(4096)
                    handle_eap_message(data.decode('utf-8', errors='ignore'))
            else: time.sleep(5)
        except Exception as e:
            log_system_event("EAP_LISTENER_ERROR", "The EAP multicast listener encountered an error.", str(e))
            time.sleep(15) # Sleep longer to prevent spamming logs on persistent errors

def broadcast_eap_message(msg_type, message):
    payload = json.dumps({"type": msg_type, "message": message}).encode('utf-8')
    subs = get_eap_subscriptions()
    sent_count = 0
    for sub in subs:
        ip = sub['ip']
        port = sub['port']
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2) # Broadcast to local subnet
            sock.sendto(payload, (ip, port))
            sock.close()
            sent_count += 1
        except Exception as e:
            print(f"[ERROR] Multicast Broadcast failed: {e}", flush=True)
    
    # Update self directly to ensure local UI reacts instantly
    handle_eap_message(payload.decode('utf-8'))
    return sent_count

@app.route('/api/eap/verify_pin', methods=['POST'])
def verify_eap_pin():
    pin = request.json.get('pin')
    with state_lock:
        expected_pin = state.get('eap_pin', '123456')
    return jsonify(success=(pin == expected_pin))

@app.route('/api/eap/broadcast', methods=['POST'])
def api_eap_broadcast():
    data = request.json
    pin = data.get('pin')
    with state_lock:
        expected_pin = state.get('eap_pin', '123456')
    if pin != expected_pin:
        return jsonify(success=False, error="Invalid PIN"), 403
    
    msg_type = data.get('type', 'EAP ALERT')
    message = data.get('message', f'Emergency Protocol Initiated: {msg_type}')
    
    log_system_event("EAP_BROADCAST", f"EAP Alert Broadcast triggered via PWA: {msg_type}")
    sent = broadcast_eap_message(msg_type, message)
    
    return jsonify(success=True, sent_to=sent)

@app.route('/api/eap/webhook', methods=['POST'])
def eap_webhook():
    # Generic inbound webhook for partners (Raptor Connect, Singlewire, CAP)
    data = request.json or {}
    msg_type = data.get('type') or data.get('IncidentType') or 'EAP ALERT'
    message = data.get('message') or 'Partner EAP Alert Received'
    
    # Forward to the local state (Partner integrations expand here later)
    handle_eap_message(json.dumps({"type": msg_type, "message": message}))
    return jsonify(success=True)

@app.route('/dispatch')
def dispatch_pwa():
    return render_template('dispatch.html')

@app.route('/sw.js')
def service_worker():
    sw_code = "self.addEventListener('install', e => e.waitUntil(self.skipWaiting())); self.addEventListener('activate', e => e.waitUntil(self.clients.claim())); self.addEventListener('fetch', e => {});"
    return sw_code, 200, {'Content-Type': 'application/javascript'}

@app.route('/api/internal/action', methods=['POST'])
def internal_action():
    # Security Check
    if not INTERNAL_API_SECRET:
        log_system_event("INTERNAL_API_ERROR", "Attempted internal API call, but no secret is configured.")
        return jsonify(success=False, error="Not configured"), 500
        
    secret = request.headers.get('X-Internal-Secret')
    if secret != INTERNAL_API_SECRET:
        log_system_event("INTERNAL_API_DENIED", "Forbidden internal API call attempt.", {"remote_addr": request.remote_addr})
        return jsonify(success=False, error="Forbidden"), 403

    data = request.json
    action = data.get('action')
    if action != 'update_host_services':
        log_system_event("INTERNAL_API_CALL", f"Received internal action: {action}", data)

    if action == 'trigger_sync':
        threading.Thread(target=run_sync, daemon=True).start()
        return jsonify(success=True, message="Sync triggered.")
        
    elif action == 'move_buddy':
        station = data.get('station')
        if station:
            # Re-use the logic from the public move_buddy endpoint
            global manual_override, override_expiry
            manual_override = station
            override_expiry = time.time() + 3600
            now = datetime.now(TZ)
            bubbles = {
                "coffee": "Grabbing a brew...", "office": "Compiling data...", 
                "gym": "Gaining processing power...", "store": "Running errands...", 
                "library": "Parsing archives...", "garage": "Diagnostic mode...", 
                "park": "Nature protocol engaged...", "kitchen": "Refueling...", 
                "bed": "Powering down..."
            }
            with state_lock:
                state.update({"station": station, "is_sleeping": (station == "bed"), "bubble": bubbles.get(station, "Rerouting..."), "acc_css": "none" if station != "bed" else "zzz", "show_bed": (station == "bed" or now.hour >= 21 or now.hour < 6)})
                try:
                    with open(STATE_FILE, 'w') as sf: json.dump(state, sf)
                except: pass
            return jsonify(success=True, message=f"Buddy moved to {station}.")
        else:
            return jsonify(success=False, error="Missing station"), 400

    elif action == 'set_emergency':
        with state_lock:
            state['emergency'] = {
                "active": data.get('active', False),
                "message": data.get('message', ''),
                "color": data.get('color', '#ff0000')
            }
            try:
                with open(STATE_FILE, 'w') as sf: json.dump(state, sf)
            except: pass
        return jsonify(success=True, message="Emergency state updated.")

    elif action == 'update_host_services':
        with state_lock:
            state['host_services'] = data.get('services', [])
        return jsonify(success=True, message="Host services updated.")

    return jsonify(success=False, error="Unknown action"), 400

@app.before_request
def check_disabled_pages():
    if request.path.startswith('/cooladmin') or request.path.startswith('/joeyadmin') or request.path.startswith('/admin') or request.path.startswith('/static') or request.path.startswith('/api/') or request.path.startswith('/docs'):
        return
    with state_lock:
        disabled = state.get("disabled_pages", [])
    if request.path in disabled or (request.path != '/' and request.path.rstrip('/') in disabled):
        return "<body style='background:#010103; color:#00ffff; font-family:monospace; display:flex; flex-direction:column; justify-content:center; align-items:center; height:100vh; margin:0;'><h2>[ SYSTEM OFFLINE ]</h2><p style='color:#fff; opacity:0.5;'>This page has been temporarily disabled.</p></body>", 503

@app.route('/docs')
@app.route('/docs/<path:filename>')
def serve_docs(filename='index'):
    if filename.endswith('.html'):
        safe_name = secure_filename(filename)
        safe_path = os.path.join(BASE_DIR, 'docs', safe_name)
        if os.path.exists(safe_path):
            return send_from_directory(os.path.join(BASE_DIR, 'docs'), safe_name)
            
    safe_name = secure_filename(filename.replace('/', '_'))
    safe_path = os.path.join(BASE_DIR, 'docs', f"{safe_name}.md")
    if not os.path.exists(safe_path):
        if os.path.exists(os.path.join(BASE_DIR, 'docs', 'index.md')):
            safe_path = os.path.join(BASE_DIR, 'docs', 'index.md')
        else:
            return "Documentation not found.", 404
            
    with open(safe_path, 'r', encoding='utf-8') as f:
        md_content = f.read()
        
    html_content = markdown.markdown(md_content, extensions=['extra', 'toc'])
    return render_template('doc_viewer.html', content=html_content, title=filename.replace('_', ' ').title())

@app.route('/enterprise')
@app.route('/sales')
def sales_landing():
    return send_from_directory(os.path.join(BASE_DIR, 'docs'), 'sales_landing.html')

@app.route('/architecture-pitch')
def architecture_pitch():
    return send_from_directory(os.path.join(BASE_DIR, 'docs'), 'architecture-pitch.html')

@app.route('/favicon.ico')
@app.route('/manifest.json')
def web_manifest():
    return jsonify({
        "name": "BEACON Dispatch",
        "short_name": "Dispatch",
        "start_url": "/dispatch",
        "display": "standalone",
        "background_color": "#050510",
        "theme_color": "#d32f2f",
        "icons": [{
            "src": "/favicon.ico",
            "sizes": "192x192",
            "type": "image/x-icon"
        }]
    })
def favicon():
    fav_path = os.path.join(app.root_path, 'static', 'favicon.ico')
    if os.path.exists(fav_path):
        return send_from_directory(os.path.join(app.root_path, 'static'), 'favicon.ico', mimetype='image/vnd.microsoft.icon')
    return "", 204

@app.route('/login')
def login_page():
    try:
        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
            c = conn.cursor()
            c.execute("SELECT provider FROM sso_configs WHERE enabled=1")
            ssos = [r[0] for r in c.fetchall()]
    except: ssos = []
    if not ssos: return "SSO is not enabled. Please use basic authentication for /cooladmin.", 403
    
    buttons = "".join([f"<a href='/login/{p.lower()}' style='display:block; padding:10px; background:#00ffff; color:#000; text-decoration:none; text-align:center; margin-bottom:10px; border-radius:4px; font-weight:bold;'>Login with {p}</a>" for p in ssos])
    buttons += "<br><a href='/cooladmin?basic_auth=true' style='display:block; text-align:center; color:#00aaaa; font-size:0.9rem; text-decoration:none;'>Local Admin Bypass</a>"
    return f"<body style='background:#050510; color:#00ffff; font-family:monospace; display:flex; justify-content:center; align-items:center; height:100vh; margin:0;'><div style='background:rgba(0,50,50,0.2); border:1px solid #00ffff; padding:30px; border-radius:8px; box-shadow:0 0 15px rgba(0,255,255,0.1); width:300px;'><h2 style='text-align:center; margin-top:0;'>BEACON SSO</h2>{buttons}</div></body>"

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

@app.route('/login/microsoft')
def login_microsoft():
    try:
        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
            c = conn.cursor()
            c.execute("SELECT client_id, client_secret, extra_info FROM sso_configs WHERE provider='Microsoft' AND enabled=1")
            row = c.fetchone()
    except: row = None
    if not row: return "Microsoft SSO not configured.", 400
    client_id, client_secret, tenant_id = row
    authority = f"https://login.microsoftonline.com/{tenant_id or 'common'}"
    msal_app = msal.ConfidentialClientApplication(client_id, authority=authority, client_credential=client_secret)
    redirect_uri = url_for("auth_callback", provider="microsoft", _external=True)
    auth_url = msal_app.get_authorization_request_url(scopes=["User.Read"], redirect_uri=redirect_uri)
    return redirect(auth_url)

@app.route('/auth/callback/<provider>')
def auth_callback(provider):
    if provider == "microsoft":
        try:
            with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                c = conn.cursor()
                c.execute("SELECT client_id, client_secret, extra_info FROM sso_configs WHERE provider='Microsoft' AND enabled=1")
                row = c.fetchone()
        except: row = None
        if not row: return "SSO Error", 400
        client_id, client_secret, tenant_id = row
        authority = f"https://login.microsoftonline.com/{tenant_id or 'common'}"
        msal_app = msal.ConfidentialClientApplication(client_id, authority=authority, client_credential=client_secret)
        result = msal_app.acquire_token_by_authorization_code(
            request.args.get('code'), scopes=["User.Read"], redirect_uri=url_for("auth_callback", provider="microsoft", _external=True)
        )
        if "id_token_claims" in result:
            claims = result.get("id_token_claims")
            user_id = claims.get("preferred_username") or claims.get("email") or claims.get("upn")
            if user_id:
                session["user"] = user_id
                session["provider"] = "Microsoft"
                try:
                    with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                        c = conn.cursor()
                        c.execute("SELECT role FROM rbac_users WHERE username=? AND type='User'", (user_id,))
                        r_row = c.fetchone()
                        session["role"] = r_row[0] if r_row else "Viewer"
                except: session["role"] = "Viewer"
                return redirect('/cooladmin')
    return "SSO Authentication Failed", 400

@app.route('/')
def index():
    with state_lock: return render_template('index.html', build_timestamp=os.environ.get("BUILD_TIMESTAMP", "Local Dev"), **state.copy())

@app.route('/index')
@app.route('/index.html')
def index_redirect():
    return redirect('/')

@app.route('/sault_weather.html')
@app.route('/sault-weather')
def sault_weather_redirect():
    return redirect('/')

@app.route('/sault-schools')
def sault_schools():
    with state_lock: 
        page_state = state.get('tenants', {}).get('sault-schools', state).copy()
        page_state['school_alerts'] = state.get('school_alerts', {})
        page_state['school_closings'] = state.get('school_closings', {})
        return render_template('sault_schools.html', **page_state)

@app.route('/sault_schools')
@app.route('/sault_schools.html')
@app.route('/sault_schools.html/')
def sault_schools_redirect():
    return redirect('/sault-schools')

@app.route('/pickford-schools')
def pickford_schools():
    with state_lock: 
        page_state = state.get('tenants', {}).get('pickford-schools', state).copy()
        page_state['school_alerts'] = state.get('school_alerts', {})
        page_state['school_closings'] = state.get('school_closings', {})
        return render_template('pickford_schools.html', **page_state)

@app.route('/pickford_schools')
@app.route('/pickford_schools.html')
@app.route('/pickford_schools.html/')
def pickford_schools_redirect():
    return redirect('/pickford-schools')

@app.route('/schools/<slug>')
def dynamic_school(slug):
    pages = get_beacon_pages()
    page = next((p for p in pages if p['slug'] == slug), None)
    if not page:
        return "Page not found", 404
    
    with state_lock: 
        page_state = state.get('tenants', {}).get(slug, state).copy()
        page_state['page_title'] = page['title']
        page_state['page_slug'] = slug
        page_state['school_alerts'] = state.get('school_alerts', {})
        page_state['school_closings'] = state.get('school_closings', {})
        return render_template('school_dashboard.html', **page_state)

@app.route('/cooladmin', methods=['GET', 'POST'])
@app.route('/joeyadmin', methods=['GET', 'POST']) # Legacy support
def cooladmin():
    auth = request.authorization
        
    is_basic = auth and auth.username == admin_username and auth.password == admin_password
    is_sso = session.get("role") == "Admin"
    
    if not (is_basic or is_sso):
        try:
            with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                c = conn.cursor()
                c.execute("SELECT provider FROM sso_configs WHERE enabled=1")
                enabled_ssos = [r[0] for r in c.fetchall()]
        except: enabled_ssos = []
        
        force_basic = request.args.get("basic_auth") == "true"
        if enabled_ssos and not force_basic:
            return redirect('/login')
        return "Administrator Access Denied", 401, {'WWW-Authenticate': 'Basic realm="CoolAdmin Login Required"'}

    cleanup_summary = None

    if request.method == 'POST':
        action = request.form.get('action')
        audit_details = request.form.to_dict()
        if 'password' in audit_details: audit_details['password'] = '***'
        user_id = session.get("user") if session.get("user") else (auth.username if auth else "unknown")
        log_audit_event(user_id, action, audit_details)
        
        if action == 'delete_pulse':
            pulse_text = request.form.get('pulse_text')
            if pulse_text:
                trigger_sync = False
                try:
                    with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                        with conn:
                            conn.execute("DELETE FROM pulses WHERE text = ?", (pulse_text,))
                except Exception as e:
                    pass
                
                with state_lock:
                    if state.get('pulse') == pulse_text:
                        state['pulse'] = ""
                        state['bubble'] = "Recalibrating pulse..."
                        trigger_sync = True
                    state['pulse_history'] = load_history()
                    try:
                        with open(STATE_FILE, 'w') as sf: json.dump(state, sf)
                    except: pass
                
                if trigger_sync:
                    threading.Thread(target=run_sync).start()
            return redirect('/cooladmin')
            
        elif action == 'toggle_page':
            page_route = request.form.get('page_route')
            if page_route:
                with state_lock:
                    if 'disabled_pages' not in state:
                        state['disabled_pages'] = []
                    if page_route in state['disabled_pages']:
                        state['disabled_pages'].remove(page_route)
                    else:
                        state['disabled_pages'].append(page_route)
                    try:
                        with open(STATE_FILE, 'w') as sf: json.dump(state, sf)
                    except: pass
            return redirect('/cooladmin')
            
        elif action == 'add_beacon_page':
            slug = request.form.get('slug', '').strip().lower().replace(' ', '-')
            title = request.form.get('title', '').strip()
            zipcode = request.form.get('zipcode', '').strip()
            if slug and title and zipcode:
                try:
                    with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                        with conn:
                            conn.execute("INSERT INTO beacon_pages (slug, title, zipcode) VALUES (?, ?, ?)", (slug, title, zipcode))
                except sqlite3.IntegrityError:
                    pass
            return redirect('/cooladmin')
        elif action == 'delete_beacon_page':
            page_id = request.form.get('page_id')
            try:
                with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                    with conn:
                        conn.execute("DELETE FROM beacon_pages WHERE id = ?", (page_id,))
            except: pass
            return redirect('/cooladmin')

        elif action == 'add_eap_sub':
            ip = request.form.get('multicast_ip', '').strip()
            port = request.form.get('port', type=int)
            profile = request.form.get('profile', 'CommonSense')
            if ip and port:
                with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                    with conn:
                        conn.execute("INSERT INTO eap_subscriptions (multicast_ip, port, profile) VALUES (?, ?, ?)", (ip, port, profile))
            return redirect('/cooladmin')

        elif action == 'delete_eap_sub':
            sub_id = request.form.get('sub_id')
            with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                with conn:
                    conn.execute("DELETE FROM eap_subscriptions WHERE id = ?", (sub_id,))
            return redirect('/cooladmin')
            
        elif action == 'update_eap_pin':
            new_pin = request.form.get('eap_pin', '').strip()
            if new_pin and len(new_pin) == 6 and new_pin.isdigit():
                with state_lock:
                    state['eap_pin'] = new_pin
                    try:
                        with open(STATE_FILE, 'w') as sf: json.dump(state, sf)
                    except: pass
            return redirect('/cooladmin')

        elif action == 'update_sso':
            provider = request.form.get('provider')
            enabled = request.form.get('enabled') == 'yes'
            client_id = request.form.get('client_id', '').strip()
            client_secret = request.form.get('client_secret', '').strip()
            extra_info = request.form.get('extra_info', '').strip()
            with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                with conn:
                    conn.execute("INSERT OR REPLACE INTO sso_configs (provider, enabled, client_id, client_secret, extra_info) VALUES (?, ?, ?, ?, ?)",
                                 (provider, enabled, client_id, client_secret, extra_info))
            return redirect('/cooladmin')
        elif action == 'add_rbac_user':
            username = request.form.get('username', '').strip()
            rtype = request.form.get('type', 'User')
            override_group = request.form.get('override_group') == 'yes'
            if username:
                try:
                    with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                        with conn:
                            conn.execute("INSERT OR REPLACE INTO rbac_users (username, role, provider, type, override_group) VALUES (?, ?, ?, ?, ?)", 
                                         (username, request.form.get('role', 'Viewer'), request.form.get('provider', 'Local'), rtype, override_group))
                except Exception as e:
                    print(e, flush=True)
            return redirect('/cooladmin')
        elif action == 'delete_rbac_user':
            with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                with conn:
                    conn.execute("DELETE FROM rbac_users WHERE id = ?", (request.form.get('user_id'),))
            return redirect('/cooladmin')

        elif action == 'shutdown':
            confirm_user = request.form.get('username')
            confirm_pass = request.form.get('password')
            if auth and confirm_user == admin_username and confirm_pass == admin_password:
                import signal
                log_system_event("SHUTDOWN", "Nuclear option invoked by cooladmin.")
                def kill_server():
                    time.sleep(1)
                    if 'gunicorn' in os.environ.get('SERVER_SOFTWARE', '').lower():
                        try:
                            os.kill(os.getppid(), signal.SIGTERM)
                        except: pass
                    os.kill(os.getpid(), signal.SIGTERM)
                threading.Thread(target=kill_server).start()
                return "<body style='background:#010103; color:#ff0000; font-family:monospace; display:flex; flex-direction:column; justify-content:center; align-items:center; height:100vh; margin:0;'><h2>[ SYSTEM OFFLINE ]</h2><p style='color:#fff; opacity:0.5;'>The server is shutting down.</p></body>", 200
            else:
                return "Invalid credentials.", 403
                
        elif action == 'run_cleanup':
            import subprocess
            try:
                res = subprocess.run(["python", os.path.join(BASE_DIR, "cleanup_hallucinations.py")], capture_output=True, text=True, check=False)
                cleanup_summary = res.stdout
            except Exception as e:
                cleanup_summary = f"Error running cleanup: {e}"
        
    with state_lock:
        service_status = state.get('host_services', [])
        if not service_status:
            service_status = [{"name": "Host Services", "active": False, "status": "pending", "context": "Waiting for host D-Bus listener to report..."}]
            
    try:
        with closing(sqlite3.connect(LOG_DB_FILE, timeout=10)) as conn:
            c = conn.cursor()
            c.execute("SELECT timestamp, load_avg, mem_used_mb, cache_mb FROM metrics WHERE timestamp >= datetime('now', '-7 days') ORDER BY timestamp ASC")
            metrics = [{"time": r[0], "load": r[1], "mem": r[2], "cache": r[3]} for r in c.fetchall()]
    except:
        metrics = []
        
    with state_lock:
        current_pulse = state.get('pulse', '')
        pulse_history = state.get('pulse_history', [])
        disabled_pages = state.get('disabled_pages', [])
        eap_pin = state.get('eap_pin', '123456')

    try:
        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
            c = conn.cursor()
            c.execute("SELECT provider, enabled, client_id, client_secret, extra_info FROM sso_configs")
            sso_configs = [{"provider": r[0], "enabled": bool(r[1]), "client_id": r[2], "client_secret": r[3], "extra_info": r[4]} for r in c.fetchall()]
            c.execute("SELECT id, username, role, provider, type, override_group FROM rbac_users")
            rbac_users = [{"id": r[0], "username": r[1], "role": r[2], "provider": r[3], "type": r[4], "override_group": bool(r[5])} for r in c.fetchall()]
    except:
        sso_configs = []
        rbac_users = []

    try:
        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
            c = conn.cursor()
            c.execute("SELECT id, retrieved_at, date, text, reason FROM hallucinations_log ORDER BY retrieved_at DESC")
            hallucinations = [{"id": r[0], "retrieved_at": r[1], "date": r[2], "text": r[3], "reason": r[4]} for r in c.fetchall()]
    except:
        hallucinations = []
        
    site_hierarchy = [
        {"name": "Main Dashboard", "url": "/"},
        {"name": "Sault Schools", "url": "/sault-schools"},
        {"name": "Pickford Schools", "url": "/pickford-schools"},
        {"name": "3D Sandbox (Buddy's World)", "url": "/rpg"},
        {"name": "EAP Dispatch (PWA)", "url": "/dispatch"},
        {"name": "Documentation Hub", "url": "/docs"},
        {"name": "Enterprise Sales Landing", "url": "/enterprise"},
        {"name": "Architecture Pitch Deck", "url": "/architecture-pitch"},
        {"name": "Admin Dashboard", "url": "/admin"},
        {"name": "Super Admin (CoolAdmin)", "url": "/cooladmin"},
    ]
    
    for page in get_beacon_pages():
        site_hierarchy.append({"name": f"{page['title']} (Custom)", "url": f"/schools/{page['slug']}"})

    return render_template('joeyadmin.html', services=service_status, metrics=metrics, beacon_pages=get_beacon_pages(), eap_subs=get_eap_subscriptions(), current_pulse=current_pulse, pulse_history=pulse_history, disabled_pages=disabled_pages, hallucinations=hallucinations, cleanup_summary=cleanup_summary, site_hierarchy=site_hierarchy, sso_configs=sso_configs, rbac_users=rbac_users, eap_pin=eap_pin)

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    is_sso_editor = session.get("role") in ["Admin", "Editor", "Sales"]
    if request.method == 'POST':
        if request.form.get('password') == admin_password or is_sso_editor:
            action = request.form.get('action')
            audit_details = request.form.to_dict()
            if 'password' in audit_details: audit_details['password'] = '***'
            user_id = session.get("user") if session.get("user") else "local_admin"
            log_audit_event(user_id, action, audit_details)
            
            with state_lock:
                # Capture current state to the undo stack prior to modifying it natively!
                if action in ['add_text_slide', 'add_image_slide', 'add_video_slide', 'add_iframe_slide', 'move_slide_up', 'move_slide_down', 'toggle_slide', 'delete_slide', 'update_dashboard_slide']:
                    slide_history['undo'].append(copy.deepcopy(state.get('slides', [])))
                    slide_history['redo'].clear()

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
                elif action == 'update_main_config':
                    state['main_config'] = {
                        "header": request.form.get('header_text', 'MORROW EDGE | BEACON Buddy').strip(),
                        "location": request.form.get('location_text', 'SAULT STE. MARIE, MICHIGAN').strip(),
                        "query": request.form.get('query_text', 'Sault+Ste.+Marie,MI,US').strip()
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
                elif action == 'add_video_slide':
                    slide = {
                        "id": str(int(time.time())),
                        "type": "video",
                        "url": request.form.get('url', '').strip(),
                        "pip": request.form.get('pip') == 'yes',
                        "duration": int(request.form.get('duration', 15)),
                        "start_time": request.form.get('start_time', ''),
                        "end_time": request.form.get('end_time', '')
                    }
                    state.setdefault('slides', []).append(slide)
                elif action == 'add_iframe_slide':
                    raw_url = request.form.get('url', '').strip()
                    parsed_url = raw_url
                    content_type = request.form.get('content_type', 'custom')
                    autoplay_req = request.form.get('autoplay') == 'yes'
                    
                    if '<iframe' in raw_url.lower() or '&lt;iframe' in raw_url.lower():
                        import html
                        raw_url = html.unescape(raw_url)
                        match = re.search(r'<iframe.*?src=["\'](.*?)["\']', raw_url, re.IGNORECASE | re.DOTALL)
                        if match:
                            parsed_url = match.group(1)

                    if 'canva.com/design' in parsed_url and '/view' in parsed_url:
                        if 'embed' not in parsed_url:
                            if '?' in parsed_url:
                                parsed_url = parsed_url.split('?')[0] + '?embed'
                            else:
                                parsed_url += '?embed'
                                
                    if 'youtube.com' in parsed_url or 'youtu.be' in parsed_url:
                        if autoplay_req:
                            if 'autoplay=1' not in parsed_url:
                                sep = '&' if '?' in parsed_url else '?'
                                parsed_url += f"{sep}autoplay=1&mute=1"
                        else:
                            parsed_url = parsed_url.replace('autoplay=1', 'autoplay=0').replace('mute=1', '')
                        if 'controls=' not in parsed_url:
                            parsed_url += "&controls=0"

                    # Handle Google Slides embed format automatically
                    if 'docs.google.com/presentation/d/' in parsed_url:
                        if '/edit' in parsed_url:
                            parsed_url = parsed_url.split('/edit')[0] + '/embed'
                        if autoplay_req and '?start=true' not in parsed_url:
                            parsed_url += '?start=true&loop=true&delayms=3000'

                    slide = {
                        "id": str(int(time.time())),
                        "type": "iframe",
                        "url": parsed_url,
                        "content_type": content_type,
                        "pip": request.form.get('pip') == 'yes',
                        "autoplay": autoplay_req,
                        "duration": int(request.form.get('duration', 15)),
                        "canva_total_slides": int(request.form.get('canva_total_slides', 1)),
                        "canva_slide_duration": int(request.form.get('canva_slide_duration', 10)),
                        "refresh_3am": request.form.get('refresh_3am') == 'yes',
                        "start_time": request.form.get('start_time', ''),
                        "end_time": request.form.get('end_time', '')
                    }
                    state.setdefault('slides', []).append(slide)
                elif action == 'move_slide_up':
                    sid = request.form.get('slide_id')
                    slides = state.get('slides', [])
                    idx = next((i for i, s in enumerate(slides) if s.get('id') == sid), -1)
                    if idx > 0:
                        slides[idx], slides[idx-1] = slides[idx-1], slides[idx]
                        state['slides'] = slides
                elif action == 'move_slide_down':
                    sid = request.form.get('slide_id')
                    slides = state.get('slides', [])
                    idx = next((i for i, s in enumerate(slides) if s.get('id') == sid), -1)
                    if idx != -1 and idx < len(slides) - 1:
                        slides[idx], slides[idx+1] = slides[idx+1], slides[idx]
                        state['slides'] = slides
                elif action == 'toggle_slide':
                    sid = request.form.get('slide_id')
                    slides = state.get('slides', [])
                    for s in slides:
                        if s.get('id') == sid:
                            s['hidden'] = not s.get('hidden', False)
                    state['slides'] = slides
                elif action == 'update_dashboard_slide':
                    slides = state.get('slides', [])
                    for s in slides:
                        if s.get('type') == 'dashboard':
                            s['duration'] = int(request.form.get('duration', 15))
                    state['slides'] = slides
                elif action == 'undo_slides':
                    if slide_history['undo']:
                        slide_history['redo'].append(copy.deepcopy(state.get('slides', [])))
                        state['slides'] = slide_history['undo'].pop()
                elif action == 'redo_slides':
                    if slide_history['redo']:
                        slide_history['undo'].append(copy.deepcopy(state.get('slides', [])))
                        state['slides'] = slide_history['redo'].pop()
                elif action == 'delete_slide':
                    sid = request.form.get('slide_id')
                    for s in state.get('slides', []):
                        if s.get('id') == sid and s.get('type') == 'image':
                            try:
                                os.remove(os.path.join(app.root_path, s.get('url').lstrip('/')))
                            except: pass
                    state['slides'] = [s for s in state.get('slides', []) if s.get('id') != sid]
                elif action == 'add_beacon_page':
                    slug = request.form.get('slug', '').strip().lower().replace(' ', '-')
                    title = request.form.get('title', '').strip()
                    zipcode = request.form.get('zipcode', '').strip()
                    if slug and title and zipcode:
                        try:
                            with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                                with conn:
                                    conn.execute("INSERT INTO beacon_pages (slug, title, zipcode) VALUES (?, ?, ?)", (slug, title, zipcode))
                        except sqlite3.IntegrityError:
                            pass # Slug already exists
                elif action == 'delete_beacon_page':
                    page_id = request.form.get('page_id')
                    try:
                        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                            with conn:
                                conn.execute("DELETE FROM beacon_pages WHERE id = ?", (page_id,))
                    except: pass
                elif action == 'update_school_alert':
                    slug = request.form.get('location_slug')
                    alert_type = request.form.get('alert_type')
                    message = request.form.get('alert_message', '').strip()
                    color = request.form.get('alert_color', '#d32f2f')
                    if 'school_alerts' not in state:
                        state['school_alerts'] = {}
                    if alert_type == 'NONE':
                        if slug in state['school_alerts']:
                            del state['school_alerts'][slug]
                    else:
                        state['school_alerts'][slug] = {
                            'type': alert_type,
                            'color': color,
                            'message': message
                        }
                try:
                    with open(STATE_FILE, 'w') as sf: json.dump(state, sf)
                except: pass
            return redirect('/admin')
        return "Unauthorized", 401
    with state_lock:
        slides = state.get('slides')
        if not isinstance(slides, list):
            slides = []
            state['slides'] = slides
            
        if not any(isinstance(s, dict) and s.get('type') == 'dashboard' for s in slides):
            state['slides'].insert(0, {"id": "dashboard", "type": "dashboard", "duration": 15, "hidden": False})
            try:
                with open(STATE_FILE, 'w') as sf: json.dump(state, sf)
            except: pass
            
        return render_template('admin.html', 
            emergency=state.get('emergency') or {}, 
            branding=state.get('branding') or {}, 
            main_config=state.get('main_config') or {}, 
            slides=state.get('slides') or [], 
            managed_theme=state.get('managed_theme', ''), 
            beacon_pages=get_beacon_pages(), 
            school_alerts=state.get('school_alerts') or {}
        )
@app.route('/api/state')
def get_state(): 
    with state_lock:
        out = state.copy()
        out["build_timestamp"] = os.environ.get("BUILD_TIMESTAMP", "Local Dev")
        out["agenda_item_count"] = state.get("agenda_item_count", 0)
        return jsonify(out)

@app.route('/api/vote', methods=['POST'])
def submit_vote():
    data = request.json
    if not data: return jsonify(success=False), 400
    item_id = data.get("item_id")
    vote_type = data.get("vote_type")
    if item_id and vote_type in ['up', 'down']:
        with state_lock:
            if 'agenda_votes' not in state:
                state['agenda_votes'] = {}
            if item_id not in state['agenda_votes']:
                state['agenda_votes'][item_id] = {'up': 0, 'down': 0}
            state['agenda_votes'][item_id][vote_type] += 1
            try:
                with open(STATE_FILE, 'w') as sf: json.dump(state, sf)
            except: pass
    return jsonify(success=True)

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

@app.route('/api/metrics')
def get_metrics_data():
    try:
        with closing(sqlite3.connect(LOG_DB_FILE, timeout=10)) as conn:
            c = conn.cursor()
            c.execute("SELECT timestamp, load_avg, mem_used_mb, cache_mb FROM metrics WHERE timestamp >= datetime('now', '-1 day') ORDER BY timestamp ASC")
            metrics_data = [{"time": r[0], "load_avg": r[1], "mem_used_mb": r[2], "cache_mb": r[3]} for r in c.fetchall()]
            return jsonify(metrics_data)
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
    threading.Thread(target=hallucination_cleanup_loop, daemon=True).start()
    threading.Thread(target=eap_multicast_listener, daemon=True).start()

if __name__ == '__main__':
    threading.Thread(target=sync_loop, daemon=True).start()
    threading.Thread(target=monitor_loop, daemon=True).start()
    threading.Thread(target=hallucination_cleanup_loop, daemon=True).start()
    threading.Thread(target=eap_multicast_listener, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, debug=False)
