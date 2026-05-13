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
import html
import smtplib
from email.mime.text import MIMEText
import google.genai as genai
from google.genai import types
import socket, struct, select
import pytz
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import markdown
import msal

def send_alert_email(subject, body, to_email="joseph@morrowedge.com"):
    try:
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = os.environ.get("SMTP_USER", "buddy-alerts@morrowedge.com")
        msg['To'] = to_email
        
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
api_alerts = {"last_sent": 0}

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
slide_history = {}
admin_username = os.environ.get("ADMIN_USERNAME", "admin")
admin_password = os.environ.get("ADMIN_PASSWORD", "changeme")
INTERNAL_API_SECRET = os.environ.get("INTERNAL_API_SECRET")
DENYLIST = ["profanity", "badword", "controversial", "inappropriate"]
last_deep_search = {}
_best_models_cache = []

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
            try:
                pulse_conn.execute("ALTER TABLE beacon_pages ADD COLUMN expires_at DATETIME")
            except sqlite3.OperationalError:
                pass
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
            try:
                pulse_conn.execute("ALTER TABLE old_pulses ADD COLUMN location TEXT DEFAULT ''")
                pulse_conn.execute("ALTER TABLE old_pulses ADD COLUMN details TEXT DEFAULT '{}'")
            except sqlite3.OperationalError:
                pass
            try:
                pulse_conn.execute("ALTER TABLE pulses ADD COLUMN location TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            pulse_conn.execute('''CREATE TABLE IF NOT EXISTS garage_sales (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                date TEXT,
                                text TEXT UNIQUE,
                                location TEXT
                             )''')
            pulse_conn.execute('''CREATE TABLE IF NOT EXISTS prompts (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                prompt_type TEXT,
                                prompt_text TEXT,
                                is_default BOOLEAN DEFAULT 0
                             )''')
            pulse_conn.execute('''CREATE TABLE IF NOT EXISTS sault_tribe (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                date TEXT,
                                text TEXT UNIQUE,
                                location TEXT
                             )''')
            pulse_conn.execute('''CREATE TABLE IF NOT EXISTS sault_schools (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                date TEXT,
                                text TEXT UNIQUE,
                                location TEXT
                             )''')

            for table in ['pulses', 'garage_sales', 'sault_tribe', 'sault_schools']:
                try:
                    pulse_conn.execute(f"ALTER TABLE {table} ADD COLUMN details TEXT DEFAULT '{{}}'")
                except sqlite3.OperationalError:
                    pass

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
            pulse_conn.execute('''CREATE TABLE IF NOT EXISTS ai_training_log (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                                topic TEXT,
                                original_text TEXT,
                                original_details TEXT,
                                new_text TEXT,
                                new_details TEXT,
                                action_type TEXT,
                                gather_prompt TEXT
                             )''')
            pulse_conn.execute('''CREATE TABLE IF NOT EXISTS user_submissions (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                submitted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                                event_type TEXT,
                                text TEXT,
                                location TEXT,
                                event_date TEXT,
                                source_url TEXT,
                                submitter_email TEXT,
                                status TEXT DEFAULT 'pending'
                             )''')
            pulse_conn.execute('''CREATE TABLE IF NOT EXISTS banned_ips (
                                ip TEXT PRIMARY KEY,
                                reason TEXT,
                                reinstatement_requested BOOLEAN DEFAULT 0,
                                banned_at DATETIME DEFAULT CURRENT_TIMESTAMP
                             )''')
            pulse_conn.execute('''CREATE TABLE IF NOT EXISTS rbac_users (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                username TEXT UNIQUE,
                                role TEXT,
                                provider TEXT,
                                type TEXT DEFAULT 'User',
                                override_group BOOLEAN DEFAULT 0
                             )''')
            pulse_conn.execute('''CREATE TABLE IF NOT EXISTS vetted_sources (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                topic TEXT,
                                source_name TEXT,
                                source_url TEXT
                             )''')
            # Seed default sources
            pulse_conn.execute("INSERT OR IGNORE INTO vetted_sources (id, topic, source_name, source_url) VALUES (1, 'sault_schools', 'Athletics Calendar', 'https://soobluedevils.com')")
            pulse_conn.execute("INSERT OR IGNORE INTO vetted_sources (id, topic, source_name, source_url) VALUES (2, 'sault_schools', 'Academic Calendar', 'https://saultschools.org')")
            pulse_conn.execute("INSERT OR IGNORE INTO vetted_sources (id, topic, source_name, source_url) VALUES (3, 'pulses', 'Sault News', 'https://www.sooeveningnews.com')")
            pulse_conn.execute("INSERT OR IGNORE INTO vetted_sources (id, topic, source_name, source_url) VALUES (4, 'sault_tribe', 'Sault Tribe News', 'https://saulttribe.com/news')")
            pulse_conn.execute("INSERT OR IGNORE INTO vetted_sources (id, topic, source_name, source_url) VALUES (5, 'pulses', '9&10 News Sault', 'https://www.9and10news.com')")
            try:
                pulse_conn.execute("ALTER TABLE rbac_users ADD COLUMN type TEXT DEFAULT 'User'")
                pulse_conn.execute("ALTER TABLE rbac_users ADD COLUMN override_group BOOLEAN DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            try:
                pulse_conn.execute("ALTER TABLE rbac_users ADD COLUMN password TEXT")
            except sqlite3.OperationalError:
                pass
            try:
                pulse_conn.execute("ALTER TABLE rbac_users ADD COLUMN last_login DATETIME")
            except sqlite3.OperationalError:
                pass
            
            pulse_conn.execute('''CREATE TABLE IF NOT EXISTS scheduled_sources (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                name TEXT,
                                target_table TEXT,
                                scrape_url TEXT,
                                schedule_type TEXT,
                                schedule_details TEXT,
                                prompt_text TEXT,
                                last_run DATETIME,
                                is_active BOOLEAN DEFAULT 1
                             )''')
            # Seed the default dynamic schools to replace the old static ones
            pulse_conn.execute("INSERT OR IGNORE INTO beacon_pages (slug, title, zipcode) VALUES ('sault-schools', 'Sault Schools', '49783,US')")
            pulse_conn.execute("INSERT OR IGNORE INTO beacon_pages (slug, title, zipcode) VALUES ('pickford-schools', 'Pickford Schools', '49774,US')")
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
            try:
                c.execute("SELECT id, slug, title, zipcode, expires_at FROM beacon_pages ORDER BY title ASC")
                return [{"id": r[0], "slug": r[1], "title": r[2], "zipcode": r[3], "expires_at": r[4]} for r in c.fetchall()]
            except sqlite3.OperationalError:
                c.execute("SELECT id, slug, title, zipcode FROM beacon_pages ORDER BY title ASC")
                return [{"id": r[0], "slug": r[1], "title": r[2], "zipcode": r[3], "expires_at": None} for r in c.fetchall()]
    except Exception as e:
        print(f"[ERROR] get_beacon_pages: {e}", flush=True)
        return []

def get_vetted_sources():
    try:
        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
            c = conn.cursor()
            c.execute("SELECT id, topic, source_name, source_url FROM vetted_sources ORDER BY topic, source_name")
            return [{"id": r[0], "topic": r[1], "name": r[2], "url": r[3]} for r in c.fetchall()]
    except:
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
                        SELECT id, date, text, location, details FROM pulses 
                        WHERE date = ? OR date = ? OR id IN (
                            SELECT id FROM pulses ORDER BY id DESC LIMIT 21
                        )
                        ORDER BY id DESC
                """, (today_str, yesterday_str))
            else:
                c.execute("SELECT id, date, text, location, details FROM pulses ORDER BY id DESC LIMIT 21")
            
            res = []
            for r in c.fetchall():
                details = {}
                try:
                    if len(r) > 4 and r[4]: details = json.loads(r[4])
                except: pass
                res.append({"id": f"pulse_{r[0]}", "date": r[1], "text": r[2], "location": r[3] if len(r)>3 and r[3] else "", "details": details})
            return res
    except Exception as e:
        print(f"[ERROR] load_history: {e}", flush=True)
        return []

def load_garage_sales():
    try:
        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
            c = conn.cursor()
            c.execute("SELECT id, date, text, location, details FROM garage_sales ORDER BY id DESC LIMIT 20")
            res = []
            for r in c.fetchall():
                details = {}
                try:
                    if len(r) > 4 and r[4]: details = json.loads(r[4])
                except: pass
                res.append({"id": f"sale_{r[0]}", "date": r[1], "text": r[2], "location": r[3] if len(r)>3 and r[3] else "", "details": details})
            return res
    except Exception as e:
        print(f"[ERROR] load_garage_sales: {e}", flush=True)
        return []

def load_sault_tribe():
    try:
        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
            c = conn.cursor()
            c.execute("SELECT id, date, text, location, details FROM sault_tribe ORDER BY id DESC LIMIT 20")
            res = []
            for r in c.fetchall():
                details = {}
                try:
                    if len(r) > 4 and r[4]: details = json.loads(r[4])
                except: pass
                res.append({"id": f"tribe_{r[0]}", "date": r[1], "text": r[2], "location": r[3] if len(r)>3 and r[3] else "", "details": details})
            return res
    except Exception as e:
        print(f"[ERROR] load_sault_tribe: {e}", flush=True)
        return []

def load_sault_schools():
    try:
        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
            c = conn.cursor()
            c.execute("SELECT id, date, text, location, details FROM sault_schools ORDER BY id DESC LIMIT 20")
            res = []
            for r in c.fetchall():
                details = {}
                try:
                    if len(r) > 4 and r[4]: details = json.loads(r[4])
                except: pass
                res.append({"id": f"school_{r[0]}", "date": r[1], "text": r[2], "location": r[3] if len(r)>3 and r[3] else "", "details": details})
            return res
    except Exception as e:
        print(f"[ERROR] load_sault_schools: {e}", flush=True)
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

def verify_events_batch(events_to_verify, is_manual=False):
    if not events_to_verify: return {}, ""
    with state_lock:
        if state.get("gemini_api_disabled", False):
            return {}, ""
    verified_details = {}
    
    if is_manual:
        check_prompt = f"""
You are an AI assistant processing MANUALLY VERIFIED local events for Sault Ste. Marie, Michigan.
DO NOT use Google Search. Trust that the user has already verified these events exist.
For EACH event, extract the 5 Ws (Who, What, Where, When, Why) based purely on the text provided.
Always set "hallucinated": false.

Input Events:
{json.dumps(events_to_verify)}

Return ONLY a valid JSON array of objects matching this exact structure:
[
  {{
    "id": "item_id_here",
    "hallucinated": false,
    "details": {{
       "who": "Person/Group involved",
       "what": "Brief description of the event",
       "where": "Specific location or address",
       "when": "Date and Time",
       "why": "Context or purpose",
       "sources": []
    }}
  }}
]
"""
        tools = None
    else:
        check_prompt = f"""
You are a strict fact-checker and investigative journalist for Sault Ste. Marie, Michigan.
I will provide a JSON list of events. For EACH event, use Google Search to verify if it actually happened or is scheduled.
If it is real/verified: Extract the 5 Ws (Who, What, Where, When, Why) and any source URLs.
If it is fake, hallucinated, or you cannot find proof: set "hallucinated": true.

Input Events:
{json.dumps(events_to_verify)}

Return ONLY a valid JSON array of objects matching this exact structure:
[
  {{
    "id": "item_id_here",
    "hallucinated": false,
    "details": {{
       "who": "Person/Group involved",
       "what": "Brief description of the event",
       "where": "Specific location or address",
       "when": "Date and Time",
       "why": "Context or purpose",
       "sources": [{{"title": "Source Name", "url": "https://..."}}]
    }}
  }}
]
"""
        tools = [{"google_search": {}}]

    global gemini_client
    if not gemini_client: gemini_client = genai.Client(api_key=G_KEY)
    for m_check in get_best_models():
        try:
            config = types.GenerateContentConfig(tools=tools) if tools else types.GenerateContentConfig()
            check_resp = gemini_client.models.generate_content(model=m_check, contents=check_prompt, config=config)
            check_text = check_resp.text
            c_start = check_text.find('[')
            c_end = check_text.rfind(']')
            if c_start == -1 or c_end == -1: raise ValueError("No JSON array in check")
            check_data = json.loads(check_text[c_start:c_end+1])
            for item in check_data: verified_details[item["id"]] = item
            break
        except Exception as e:
            if handle_gemini_error(e):
                break
            print(f"Check failed with {m_check}: {e}", flush=True)
            continue
    return verified_details, check_prompt

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
    "garage_sales": load_garage_sales(),
    "sault_tribe": load_sault_tribe(),
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
    """Augmented discovery prioritizing high-RPD models, strictly cached to prevent API spam."""
    global gemini_client, _best_models_cache
    if _best_models_cache: return _best_models_cache
    try:
        if not gemini_client: gemini_client = genai.Client(api_key=G_KEY)
        all_m = list(gemini_client.models.list())
        ranked = []
        for m in all_m:
            n = m.name.lower()
            n_clean = m.name.replace("models/", "")
            
            # Block multimodal/experimental models from draining quota on text tasks
            if any(x in n for x in ["tts", "image", "audio", "vision", "embedding", "pro", "ultra", "learnmath"]):
                continue
                
            score = 0
            if "3.1-flash" in n: score = 2000
            elif "3.0-flash" in n: score = 1800
            elif "2.5-flash" in n: score = 1500
            elif "2.0-flash-lite" in n: score = 1200
            elif "2.0-flash" in n: score = 1000
            elif "1.5-flash" in n: score = 500
            if score > 0: ranked.append((n_clean, score))
        ranked.sort(key=lambda x: x[1], reverse=True)
        _best_models_cache = [r[0] for r in ranked] if ranked else ["gemini-2.0-flash", "gemini-1.5-flash"]
        return _best_models_cache
    except Exception as e:
        print(f"[ERROR] get_best_models: {e}", flush=True)
        log_system_event("API_ERROR", "Failed to list Gemini models", str(e))
        return ["gemini-2.0-flash", "gemini-1.5-flash"]

def handle_gemini_error(e):
    err_str = str(e).lower()
    if "429" in err_str or "exhausted" in err_str or "quota" in err_str:
        with state_lock:
            already_disabled = state.get("gemini_api_disabled", False)
            if not already_disabled:
                state["gemini_api_disabled"] = True
                try:
                    with open(STATE_FILE, 'w') as sf: json.dump(state, sf)
                except: pass
        if not already_disabled:
            send_alert_email(
                "[CRITICAL - BEACON BUDDY] Gemini API Quota Exhausted",
                "The Gemini API has returned a Resource Exhausted / 429 error. AI generation has been disabled. You can re-enable it in CoolAdmin."
            )
        return True
    return False

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
        
        # Automatically correct the AI context to the true OpenWeatherMap city name!
        owm_city = w.get('name')
        if owm_city and owm_city.strip():
            loc_name = owm_city
            
        f = http_session.get(f"https://api.openweathermap.org/data/2.5/forecast?q={query}&appid={OWM_KEY}&units=imperial", timeout=10).json()
        now = datetime.now(TZ)
        h = now.hour
        is_sleep = (h >= 22 or h < 6)
        st_id = "bed" if is_sleep else next((v for k,v in {20:"kitchen", 19:"library", 17:"store", 16:"gym", 8:"office", 6:"coffee"}.items() if h >= k), "coffee")
        
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
        
        # Human-centric Precipitation Probability (PoP) Time Window
        # - Morning to Afternoon: Care about rain until ~6 PM
        # - Evening (6 PM - 11 PM): Care about rain until Midnight
        # - Late Night (11 PM+): Care about rain for the next 8 hours (waking up)
        pop_target_dt = now.replace(minute=0, second=0, microsecond=0)
        if h < 18:
            pop_target_dt = pop_target_dt.replace(hour=18)
        elif h < 23:
            pop_target_dt = (pop_target_dt + timedelta(days=1)).replace(hour=0)
        else:
            pop_target_dt = pop_target_dt + timedelta(hours=8)
            
        # Ensure we always look ahead at least 3 hours to capture imminent weather
        if (pop_target_dt - now).total_seconds() < 10800:
            pop_target_dt = now + timedelta(hours=3)

        pop_items = []
        for i in f['list']:
            dt_utc = datetime.strptime(i['dt_txt'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=pytz.utc)
            dt_local = dt_utc.astimezone(TZ)
            if dt_local > pop_target_dt:
                break
            pop_items.append(i)
            
        pop = int(max([i.get('pop', 0) for i in pop_items]) * 100) if pop_items else int(f['list'][0].get('pop', 0) * 100)
        
        # Calculate 5-Day Outlook
        daily_forecasts = {}
        for item in f['list']:
            # Convert UTC dt_txt to local timezone date string
            dt_utc = datetime.strptime(item['dt_txt'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=pytz.utc)
            dt_local = dt_utc.astimezone(TZ)
            d_str = dt_local.strftime('%Y-%m-%d')
            
            if d_str not in daily_forecasts:
                daily_forecasts[d_str] = {'high': -100, 'low': 100, 'icons': []}
            daily_forecasts[d_str]['high'] = max(daily_forecasts[d_str]['high'], item['main']['temp_max'])
            daily_forecasts[d_str]['low'] = min(daily_forecasts[d_str]['low'], item['main']['temp_min'])
            daily_forecasts[d_str]['icons'].append(item['weather'][0]['icon'].replace('n', 'd'))
            
        weekly_list = []
        tomorrow_date = (now + timedelta(days=1)).date()
        for dt_str in sorted(daily_forecasts.keys()):
            dt_obj = datetime.strptime(dt_str, '%Y-%m-%d').date()
            if dt_obj >= tomorrow_date:
                dat = daily_forecasts[dt_str]
                d_icon = max(set(dat['icons']), key=dat['icons'].count) if dat['icons'] else "01d"
                day_name = dt_obj.strftime('%a')
                date_short = dt_obj.strftime('%m/%d')
                weekly_list.append({"day": day_name, "date_short": date_short, "high": int(dat['high']), "low": int(dat['low']), "icon": d_icon})
                if len(weekly_list) == 5:
                    break
            
        is_post_midnight = now.hour < 6
        is_late_night = (now.hour == 21 and now.minute >= 30) or (now.hour >= 22) or is_post_midnight
        tomorrow_label = "later today" if is_post_midnight else "tomorrow"
        tomorrow_ui_label = "Later Today" if is_post_midnight else "Tomorrow"

        weather_desc = w['weather'][0]['description'].lower()
        severe_keywords = ["storm", "tornado", "hurricane", "flood", "thunder", "extreme", "blizzard"]
        is_severe = any(kw in weather_desc for kw in severe_keywords)
        mood_instruction = "IMPORTANT: The current weather is SEVERE. Keep Buddy's tone serious, urgent, and focused on safety. Do not be overly cheerful." if is_severe else "Buddy should be his usual helpful, friendly self."

        buddy_task = (
            "Task 1 (Bubble): 3-5 word unique greeting observing the beautiful sunrise. STRICT LIMIT: Under 6 words. DO NOT concatenate tasks here." 
            if (is_morning_golden and clouds < 75) else 
            "Task 1 (Bubble): 3-5 word ambient technical activity (e.g., 'Calibrating firmware...', 'Parsing archives...', 'Grabbing a pastie...'). STRICT LIMIT: Under 6 words. DO NOT concatenate tasks here."
        )

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
        temporal_context = " (Late Night - do not refer to evening/tonight as a future event)" if is_late_night else ""
        
        with state_lock:
            if slug == "main":
                last_pulse_topic = state.get("pulse", "")
            else:
                last_pulse_topic = state.get("tenants", {}).get(slug, {}).get("pulse", "")
        
        now_ts_sec = time.time()
        
        # DEEP SEARCH RATE LIMITER: 
        # Community event extraction and Search Grounding are heavy. Limit to once per hour.
        do_deep_search = not is_late_night and (now_ts_sec - last_deep_search.get(slug, 0) > 3600)
        
        pulse_task = (
            f"Task 2 (Pulse): Adopt the persona of a comforting, poetic night-owl. Provide a quiet 1-2 sentence late-night observation of {loc_name}'s nocturnal rhythm. Seamlessly weave a brief mention of {tomorrow_label}'s weather into this comforting thought. IMPORTANT: Because it is currently {time_str}, do NOT refer to 'evening' or 'tonight' as future events. Keep the tone warm, hushed, and safe (never eerie or lonely). Wrap specific local landmarks in <i> tags. DO NOT state the exact time. DO NOT use markdown like asterisks (**). Do not use intense or zine-like language; keep it ambient and conversational. Do not search for news. Set 'is_news' to false." 
            if is_late_night else 
            (
                f"Task 2 (Pulse): Adopt the persona of an inspiring, steadfast community leader and masterful speechwriter, focused on the indomitable human spirit. "
                f"SEARCH for recent local news, community successes, acts of kindness, or verifiable events happening TODAY in {loc_name}. If no specific news or event is found, DO NOT invent names or fake heroics; instead, offer a grounded note of gratitude for the community's resilience or a 'Seasonal Rhythm' (e.g., <i>shipping traffic</i>). CONTENT: Provide a 2-sentence update. Give a brief shoutout/kudos to a local achievement OR share the real event, weaving the current weather into this message seamlessly. Make the reader feel proud and ready to take on the day. TRUTH BOUNDARY: Only include specific details if verified. FORMATTING: Wrap specific locations, subjects, and verified event times in <i> tags. Avoid overly saccharine words. DO NOT use markdown like asterisks (**). Do not use intense or zine-like language; keep it grounded, conversational, and ambient. Set 'is_news' to true ONLY if specific verified news/events are shared; otherwise, set to false."
                if do_deep_search else
                f"Task 2 (Pulse): Adopt the persona of an inspiring, steadfast community leader. Offer a 2-sentence grounded note of gratitude for the community's resilience or a 'Seasonal Rhythm' in {loc_name} (e.g., <i>shipping traffic</i>), weaving the current weather into this message seamlessly. Make the reader feel proud and ready to take on the day. FORMATTING: Wrap specific locations in <i> tags. DO NOT use markdown like asterisks (**). Do not use intense or zine-like language; keep it grounded, conversational, and ambient. Set 'is_news' to false."
            )
        )

        vetted_srcs = get_vetted_sources()
        gs_src = [s['url'] for s in vetted_srcs if s['topic'] == 'garage_sales']
        tribe_src = [s['url'] for s in vetted_srcs if s['topic'] == 'sault_tribe']
        school_src = [s['url'] for s in vetted_srcs if s['topic'] == 'sault_schools']

        gs_extra = f" Prioritize these vetted sources: {', '.join(gs_src)}." if gs_src else ""
        st_extra = f" Prioritize these vetted sources: {', '.join(tribe_src)}." if tribe_src else ""
        ss_extra = f" Prioritize these vetted sources: {', '.join(school_src)}." if school_src else ""

        extra_tasks = ""
        json_format = '{ "tip": "attire", "bubble": "Task 1 (3-5 words ONLY)", "pulse": "vibe", "acc": "tool/none", "forecast": "summary", "weekly_summary": "outlook", "is_news": true'
        
        if do_deep_search:
            last_deep_search[slug] = now_ts_sec
            if slug == "main":
                extra_tasks = f"Task 6 (Garage Sales): SEARCH for real Garage/Yard/Estate Sales in Sault Ste. Marie, Michigan scheduled in the NEXT 7 DAYS. EXCLUDE Canadian garage sales (Sault Ste. Marie, Ontario) UNLESS it is a massive flea market. The 'location' must contain a valid street/road name.{gs_extra}\n        "
                extra_tasks += f"Task 7 (Sault Tribe): SEARCH for Sault Tribe of Chippewa Indians news, board meetings, or events in the NEXT 7 DAYS.{st_extra}\n        "
                extra_tasks += f"Task 8 (Sault Schools): SEARCH for Sault Area Public Schools events (Malcolm High, Sault High, Sault Middle, Sault Elementary) in the NEXT 7 DAYS. ONLY include public or large-gathering events (sports games, graduations, board meetings). DO NOT list private events, staff in-services, or closed gatherings.{ss_extra} If an athletic event is found, append pricing: '$2 public, $1 seniors, Free for students/faculty' unless specified otherwise.\n        "
                json_format += ', "garage_sales": [{"text": "sale info", "location": "address"}], "sault_tribe": [{"text": "news/event", "location": "location"}], "sault_schools": [{"text": "event details", "location": "location"}]'
        json_format += ' }'

        prompt = f"""
        {loc_name}. Date: {date_str}. Time: {time_str}{temporal_context}. Weather: {w['weather'][0]['description']}. Precip Chance: {pop}%. Forecast: {forecast_context}. Station: {st_id}. Sleep: {is_sleep}.
        PREVIOUS PULSE: "{last_pulse_topic}" -> Provide a completely different topic/event.
        {buddy_task}
        {pulse_task}
        {mood_instruction}
        Task 3 (Forecast): 1 short sentence summarizing today/tomorrow's weather based on forecast.
        Task 4 (Attire): 2-4 word practical clothing/gear suggestion based on the forecast. Factor in current season ({now.strftime('%B')}).
        Task 5 (Weekly): 1-2 sentence overall outlook for the upcoming 5 days based on the forecast trend.
        {extra_tasks}
        CRITICAL INSTRUCTION: Separate each task into its exact JSON key. Do not output conversational filler. Keep 'bubble' strictly 3-5 words.
        Return JSON: {json_format}
        """
        
        success = False
        new_pulse = "Anchoring Sault Pulse..." if slug == "main" else f"Anchoring {loc_name} Pulse..."
        is_news = False
        ai = {}

        tools_config = types.GenerateContentConfig(tools=[{"google_search": {}}]) if do_deep_search else types.GenerateContentConfig()
        
        with state_lock:
            gemini_disabled = state.get("gemini_api_disabled", False)
            
        if not gemini_disabled:
            for m_id in get_best_models():
                try:
                    resp = gemini_client.models.generate_content(
                        model=m_id, 
                        contents=prompt,
                        config=tools_config
                    )
                    text = resp.text
                    json_start = text.find('{')
                    json_end = text.rfind('}')
                    if json_start == -1 or json_end == -1:
                        raise ValueError("Invalid JSON boundaries from AI")
                    json_str = text[json_start:json_end+1]
                    ai = json.loads(json_str)
                    
                    new_pulse = str(ai.get("pulse", new_pulse)).replace('**', '')
                    new_pulse_loc = ai.get("location", "")
                    is_news = str(ai.get("is_news", False)).lower() in ["true", "1", "yes"]
                    ai_garage_sales = ai.get("garage_sales", [])
                    ai_sault_tribe = ai.get("sault_tribe", [])
                    ai_sault_schools = ai.get("sault_schools", [])
                    
                    events_to_verify = []
                if is_news and new_pulse and "Anchoring" not in new_pulse:
                    events_to_verify.append({"id": "pulse", "type": "pulse", "text": new_pulse})
                
                if isinstance(ai_garage_sales, list):
                    for idx, g in enumerate(ai_garage_sales): 
                        if g.get("text"): events_to_verify.append({"id": f"garage_{idx}", "type": "garage_sale", "text": g.get("text")})
                
                if isinstance(ai_sault_tribe, list):
                    for idx, t in enumerate(ai_sault_tribe): 
                        if t.get("text"): events_to_verify.append({"id": f"tribe_{idx}", "type": "sault_tribe", "text": t.get("text")})

                if isinstance(ai_sault_schools, list):
                    for idx, s in enumerate(ai_sault_schools): 
                        if s.get("text"): events_to_verify.append({"id": f"school_{idx}", "type": "sault_schools", "text": s.get("text")})

                verified_details = {}
                
                if events_to_verify:
                    v_res, _ = verify_events_batch(events_to_verify)
                    verified_details = v_res

                pulse_details = {}
                if is_news and new_pulse and "Anchoring" not in new_pulse:
                    v_res = verified_details.get("pulse", {})
                    if v_res.get("hallucinated"):
                        print(f"[API] Hallucination caught in-flight for pulse! Replacing with fallback.", flush=True)
                        import random
                        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                            c = conn.cursor()
                            c.execute("SELECT text FROM pulses ORDER BY id DESC LIMIT 18")
                            recent = [r[0] for r in c.fetchall()]
                            new_pulse = random.choice(recent) if recent and slug == "main" else f"{loc_name} continues its steady rhythm."
                        is_news = False
                    else:
                        pulse_details = v_res.get("details", {})
                        
                valid_garage_sales = []
                if isinstance(ai_garage_sales, list):
                    for idx, sale in enumerate(ai_garage_sales):
                        s_id = f"garage_{idx}"
                        v_res = verified_details.get(s_id, {})
                        if not v_res.get("hallucinated"):
                            sale['details'] = v_res.get("details", {})
                            valid_garage_sales.append(sale)
                            
                valid_sault_tribe = []
                if isinstance(ai_sault_tribe, list):
                    for idx, event in enumerate(ai_sault_tribe):
                        s_id = f"tribe_{idx}"
                        v_res = verified_details.get(s_id, {})
                        if not v_res.get("hallucinated"):
                            event['details'] = v_res.get("details", {})
                            valid_sault_tribe.append(event)
                            
                valid_sault_schools = []
                if isinstance(ai_sault_schools, list):
                    for idx, event in enumerate(ai_sault_schools):
                        s_id = f"school_{idx}"
                        v_res = verified_details.get(s_id, {})
                        if not v_res.get("hallucinated"):
                            event['details'] = v_res.get("details", {})
                            valid_sault_schools.append(event)

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
                                    conn.execute("INSERT INTO pulses (date, text, location, details) VALUES (?, ?, ?, ?)", (date_str, new_pulse, new_pulse_loc, json.dumps(pulse_details)))
                        except sqlite3.IntegrityError:
                            pass
                            
                if slug == "main":
                    def merge_or_insert(table, date_str, item_text, item_loc, item_details):
                        try:
                            with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                                c = conn.cursor()
                                c.execute(f"SELECT id, text, details FROM {table} ORDER BY id DESC LIMIT 20")  # nosec B608
                                rows = c.fetchall()
                                for r in rows:
                                    r_id, r_text, r_details = r
                                    r_det = {}
                                    try: r_det = json.loads(r_details) if r_details else {}
                                    except: pass
                                    
                                    is_dup = False
                                    if item_details.get('when') and r_det.get('when') == item_details.get('when'):
                                        if item_details.get('where') and r_det.get('where') == item_details.get('where'):
                                            is_dup = True
                                            
                                    if not is_dup:
                                        w1 = set(item_text.lower().split())
                                        w2 = set(r_text.lower().split())
                                        if w1 and w2 and len(w1.intersection(w2)) > max(3, len(w1)//2):
                                            is_dup = True
                                            
                                    if is_dup:
                                        src_map = {s.get('url'): s for s in r_det.get('sources', [])}
                                        for s in item_details.get('sources', []):
                                            if s.get('url'): src_map[s['url']] = s
                                        item_details['sources'] = list(src_map.values())
                                        c.execute(f"UPDATE {table} SET text=?, location=?, details=? WHERE id=?", (item_text, item_loc, json.dumps(item_details), r_id))  # nosec B608
                                        conn.commit()
                                        return
                                c.execute(f"INSERT INTO {table} (date, text, location, details) VALUES (?, ?, ?, ?)", (date_str, item_text, item_loc, json.dumps(item_details)))  # nosec B608
                                conn.commit()
                        except sqlite3.IntegrityError: pass
                        except Exception as e: print(f"[ERROR] merge_or_insert {table}: {e}", flush=True)

                    for sale in valid_garage_sales:
                        if sale.get("text"): merge_or_insert("garage_sales", date_str, sale.get("text"), sale.get("location", ""), sale.get("details", {}))
                                
                    for event in valid_sault_tribe:
                        if event.get("text"): merge_or_insert("sault_tribe", date_str, event.get("text"), event.get("location", ""), event.get("details", {}))
                                
                    for event in valid_sault_schools:
                        if event.get("text"): merge_or_insert("sault_schools", date_str, event.get("text"), event.get("location", ""), event.get("details", {}))
                
                print(f"[API] Success via {m_id} for {slug}", flush=True)
                success = True; break
            except Exception as e:
                if handle_gemini_error(e):
                    gemini_disabled = True
                    break
                print(f"[API] Generation failed with {m_id}: {e}", flush=True)
                log_system_event("AI_GENERATION_ERROR", f"Failed with {m_id} on {slug}", str(e))
                continue
        
        if not success:
            if gemini_disabled:
                ai["bubble"] = "Conserving energy..."
                ai["forecast"] = w['weather'][0]['description'].title()
                ai["weekly_summary"] = "Weekly pattern steady."
                try:
                    with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                        c = conn.cursor()
                        c.execute("SELECT text, location FROM pulses ORDER BY id DESC LIMIT 1")
                        row = c.fetchone()
                        if row:
                            new_pulse = row[0]
                            ai["location"] = row[1] if row[1] else ""
                except: pass
            else:
                ai["bubble"] = "Optimizing antenna..."
        
        ai['bubble'] = ai.get('bubble', '...').replace('**', '') if isinstance(ai.get('bubble'), str) else ai.get('bubble', '...')
        ai['suggestion'] = ai.get('suggestion', '').replace('**', '') if isinstance(ai.get('suggestion'), str) else ai.get('suggestion')
        ai['forecast'] = ai.get('forecast', '').replace('**', '') if isinstance(ai.get('forecast'), str) else ai.get('forecast')

        day = now.day
        suffix = 'th' if 11 <= day <= 13 else {1:'st', 2:'nd', 3:'rd'}.get(day % 10, 'th')
        
        hist = load_history(date_str, (now - timedelta(days=1)).strftime('%B %d')) if slug == "main" else []

        update_dict = {
                "temp": int(w['main']['temp']), "high": today_high, 
                "low": today_low,
                "desc": w['weather'][0]['description'].title(), "icon": w['weather'][0]['icon'],
                "date": now.strftime(f"%A, %B {day}{suffix}, %Y"), "time": now.strftime('%I:%M %p'), 
                "station": st_id, "is_sleeping": is_sleep, "show_bed": (h >= 20 or h < 6), 
                "t_high": t_high, "t_low": t_low, "t_desc": t_desc, "t_pop": t_pop,
                "tomorrow_label": tomorrow_ui_label,
                "is_late_night": is_late_night,
                "is_day": is_day, "is_golden": is_golden, "pop": pop,
                "clouds": clouds_val, "humidity": humidity_val,
                "wind": wind_str, "uv_index": uv_str,
                "hourly_list": hourly_list,
                "sunrise": sunrise_str, "sunset": sunset_str,
                "weekly_list": weekly_list,
                "suggestion": ai.get("tip") or "Stay safe.", "bubble": ai.get("say") or ai.get("bubble") or "...", 
                "pulse": new_pulse, "acc_css": "zzz" if is_sleep else (ai.get("acc") or "none"),
                "forecast": ai.get("forecast") or "Weather data processing...", 
                "weekly_summary": ai.get("weekly_summary") or "Weekly pattern steady.",
                "pulse_history": hist,
                "garage_sales": load_garage_sales(),
                "sault_tribe": load_sault_tribe(),
                "sault_schools": load_sault_schools(),
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
        current_time = time.time()
        if current_time - api_alerts.get("last_sent", 0) > 3600:
            send_alert_email(
                "[HIGH PRIORITY - BEACON BUDDY] API Integration Failure", 
                f"An unexpected error occurred during the data sync for {slug}.\n\nError Details: {str(e)}\n\nThis usually means an external API (like OpenWeatherMap) changed its response format, omitting an expected parameter.\n\nThe dashboard has auto-healed to a safe fallback state, but please review app.py to iterate on the new API schema.",
                os.environ.get("SMTP_USER", "joseph@morrowedge.com")
            )
            api_alerts["last_sent"] = current_time

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
    now_ts = time.time()
    # Scrape closings once globally
    now = datetime.now(TZ)
    sault_closed = False
    other_closings = []
    if now.month in [10, 11, 12, 1, 2, 3, 4]:
        sault_closed, other_closings = scrape_closings()
        
    with state_lock:
        state['school_closings'] = {"sault_closed": sault_closed, "other_closings": other_closings}
        state['agenda_item_count'] = get_agenda_item_count()
        
        # Handle EAP All Clear Timeouts (10 mins)
        alerts = state.get('school_alerts', {})
        keys_to_del = []
        for t, alert in alerts.items():
            if alert.get('color') == "#388e3c" or "clear" in alert.get('type', '').lower():
                if now_ts - alert.get('timestamp', now_ts) > 600:
                    keys_to_del.append(t)
        for k in keys_to_del:
            del state['school_alerts'][k]
        if keys_to_del:
            try:
                with open(STATE_FILE, 'w') as sf: json.dump(state, sf)
            except: pass

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

def fetch_annual_school_calendar():
    import calendar
    global gemini_client
    now = datetime.now(TZ)
    current_year = now.year

    with state_lock:
        last_fetch = state.get("last_calendar_fetch_year", 0)
        
    if last_fetch >= current_year:
        return

    # Calculate Memorial Day (last Monday of May)
    cal = calendar.monthcalendar(current_year, 5)
    last_monday_day = cal[-1][calendar.MONDAY] if cal[-1][calendar.MONDAY] != 0 else cal[-2][calendar.MONDAY]
    
    # 14 days before Memorial Day
    memorial_day = TZ.localize(datetime(current_year, 5, last_monday_day, 0, 0, 0))
    target_date = memorial_day - timedelta(days=14)

    if now >= target_date:
        print(f"[ANNUAL TASK] Time to fetch the Sault Schools calendar for {current_year}...", flush=True)
        try:
            from bs4 import BeautifulSoup
            res = requests.get("https://www.saultschools.org/sault-area-public-schools-calendar", timeout=15)
            soup = BeautifulSoup(res.text, 'html.parser')
            
            with state_lock:
                if state.get("gemini_api_disabled", False):
                    print("[ANNUAL TASK] Skipped due to Gemini API disabled.", flush=True)
                    return
            pdf_url = None
            for a in soup.find_all('a', href=True):
                if a['href'].lower().endswith('.pdf'):
                    pdf_url = a['href']
                    if not pdf_url.startswith('http'):
                        pdf_url = "https://www.saultschools.org" + pdf_url if pdf_url.startswith('/') else "https://www.saultschools.org/" + pdf_url
                    break
            
            if not pdf_url:
                print("[ANNUAL TASK] No PDF link found on the calendar page.", flush=True)
                return

            if not gemini_client: gemini_client = genai.Client(api_key=G_KEY)
            
            print(f"[ANNUAL TASK] Found PDF: {pdf_url}. Processing with Gemini...", flush=True)
            # Depending on Gemini SDK context, from_uri prefers Google Cloud URLs or File API URIs.
            # Passing standard HTTPS relies on the model executing a sub-fetch.
            pdf_part = types.Part.from_uri(file_uri=pdf_url, mime_type="application/pdf")
            prompt = f"""Using the attached blue calendar, extract the events. ONLY include public or large-gathering events like sports games, parent-teacher conferences, or graduations. DO NOT include private events, staff in-services, or closed gatherings.

Return ONLY a valid JSON array of objects matching this exact structure:
[
  {{
    "text": "Brief description of the event",
    "location": "Location if known, or Sault Schools",
    "date": "Month Day", 
    "details": {{
       "who": "Person/Group involved",
       "what": "Brief description of the event",
       "where": "Specific location or address",
       "when": "Date and Time",
       "why": "Context or purpose",
       "sources": [{{"title": "School Calendar", "url": "{pdf_url}"}}]
    }}
  }}
]"""

            config = types.GenerateContentConfig()
            best_models = get_best_models()
            success = False
            for m_id in best_models:
                try:
                    resp = gemini_client.models.generate_content(
                        model=m_id,
                        contents=[pdf_part, prompt],
                        config=config
                    )
                    text = resp.text
                    json_start = text.find('[')
                    json_end = text.rfind(']')
                    if json_start != -1 and json_end != -1:
                        events = json.loads(text[json_start:json_end+1])
                        
                        for ev in events:
                            event_text = ev.get('text', '')
                            event_loc = ev.get('location', '')
                            event_date = ev.get('date', '')
                            event_details = ev.get('details', {})
                            if event_text and event_date:
                                try:
                                    with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                                        c = conn.cursor()
                                        c.execute("SELECT id, text FROM sault_schools ORDER BY id DESC LIMIT 50")
                                        rows = c.fetchall()
                                        is_dup = False
                                        r_id_to_update = None
                                        for r in rows:
                                            r_id, r_text = r
                                            w1 = set(event_text.lower().split())
                                            w2 = set(r_text.lower().split())
                                            if w1 and w2 and len(w1.intersection(w2)) > max(3, len(w1)//2):
                                                is_dup = True
                                                r_id_to_update = r_id
                                                break
                                        if is_dup and r_id_to_update:
                                            c.execute("UPDATE sault_schools SET text=?, location=?, details=? WHERE id=?", (event_text, event_loc, json.dumps(event_details), r_id_to_update))
                                        else:
                                            c.execute("INSERT INTO sault_schools (date, text, location, details) VALUES (?, ?, ?, ?)", (event_date, event_text, event_loc, json.dumps(event_details)))
                                        conn.commit()
                                except Exception as e:
                                    print(f"[ANNUAL TASK] DB Error: {e}", flush=True)
                        success = True
                        break
                except Exception as e:
                    if handle_gemini_error(e):
                        break
                    print(f"[ANNUAL TASK] Gemini processing failed with {m_id}: {e}", flush=True)
                    continue

            if success:
                with state_lock:
                    state["last_calendar_fetch_year"] = current_year
                    try:
                        with open(STATE_FILE, 'w') as sf: json.dump(state, sf)
                    except: pass
                print("[ANNUAL TASK] Successfully fetched and processed school calendar.", flush=True)

        except Exception as e:
            print(f"[ANNUAL TASK] Error fetching calendar: {e}", flush=True)

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
        
        fetch_annual_school_calendar()
        
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
                with state_lock:
                    gemini_disabled = state.get("gemini_api_disabled", False)
                if gemini_disabled:
                    send_alert_email("[WARNING - BEACON BUDDY] - Memory Leak (AI Offline)", f"Top 5 allocations: {leak_details}")
                    last_leak_email_time = current_time
                    last_leak_signature = current_sig
                    baseline_snapshot = current_snapshot
                    continue
                    
                global gemini_client
                if not gemini_client: gemini_client = genai.Client(api_key=G_KEY) # AI client initialized
                prompt = (
                    f"A Python memory leak was detected. Top 5 allocations: {leak_details}. "
                    "Evaluate if this is a severe, compounding leak or normal background caching. "
                    'Return JSON only: {"critical": true, "reason": "<why>", "prompt_suggestion": "<how I should prompt you to fix it>"} '
                    'Or if safe: {"critical": false}'
                )
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
                    except Exception as e:
                        if handle_gemini_error(e):
                            break
                        continue
            except Exception as e:
                print(f"[ERROR] AI Leak eval/email failed: {e}", flush=True)

        # 4. Cleanup expired demos
        try:
            now_str = datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')
            with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                with conn:
                    conn.execute("DELETE FROM beacon_pages WHERE slug LIKE 'demo-%' AND expires_at < ?", (now_str,))
        except Exception as e:
            pass

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
        target = data.get("target", "ALL")
    except:
        msg_type = "EAP ALERT"
        msg = msg_text.strip()
        target = "ALL"

    msg_lower = msg.lower()
    color = "#d32f2f" # Default Red (Lockdown/Fire/Evacuate)
    if "secure" in msg_lower or "hazard" in msg_lower or "weather" in msg_lower: color = "#ff8c00"
    elif "hold" in msg_lower: color = "#800080"
    elif "shelter" in msg_lower or "medical" in msg_lower: color = "#1976d2"
    elif "clear" in msg_lower:
        color = "#388e3c"
        if "ALL CLEAR" not in msg_type:
            msg_type = "ALL CLEAR"

    with state_lock:
        if 'school_alerts' not in state: state['school_alerts'] = {}
        
        tenants_to_update = []
        if target == "ALL":
            tenants_to_update = ['main'] + [p['slug'] for p in get_beacon_pages()]
        elif target == "main":
            tenants_to_update = ['main']
        else:
            tenants_to_update = [target]
            
        for t in tenants_to_update:
            state['school_alerts'][t] = {'type': msg_type, 'color': color, 'message': msg, 'timestamp': time.time()}
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

def broadcast_eap_message(msg_type, message, target="ALL"):
    payload = json.dumps({"type": msg_type, "message": message, "target": target}).encode('utf-8')
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
    target = data.get('target', 'ALL')
    
    log_system_event("EAP_BROADCAST", f"EAP Alert Broadcast triggered via PWA: {msg_type}")
    sent = broadcast_eap_message(msg_type, message, target)
    
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
@app.route('/<slug>/dispatch')
@app.route('/schools/<slug>/dispatch')
@app.route('/demo/<slug>/dispatch')
def dispatch_pwa(slug="main"):
    return render_template('dispatch.html', slug=slug)

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
                "coffee": "Brewing some camp coffee...", "office": "Mapping the local trails...", 
                "gym": "Chopping digital firewood...", "store": "Grabbing some fresh pasties...", 
                "library": "Reading the old legends...", "garage": "Tuning up the sled...", 
                "park": "Watching the freighters pass...", "kitchen": "Frying up some whitefish...", 
                "bed": "Resting by the fire..."
            }
            with state_lock:
                    updates = {"station": station, "is_sleeping": (station == "bed"), "bubble": bubbles.get(station, "Rerouting..."), "acc_css": "none" if station != "bed" else "zzz", "show_bed": (station == "bed")}
                    state.update(updates)
                    if 'tenants' in state:
                        for tenant in state['tenants'].values():
                            tenant.update(updates)
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

@app.route('/api/submit_event', methods=['POST'])
def api_submit_event():
    data = request.json
    if not data: return jsonify(success=False, error="No data"), 400
    
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip()
    
    with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
        c = conn.cursor()
        c.execute("SELECT reinstatement_requested FROM banned_ips WHERE ip=?", (client_ip,))
        row = c.fetchone()
        if row:
            return jsonify(success=False, error="Your IP has been blocked due to suspicious activity.", banned=True, requested=bool(row[0])), 403

    # 1. Honeypot & Time-of-Flight Validation
    is_bot = False
    if data.get('website_url'): is_bot = True
        
    try:
        if time.time() - float(data.get('rendered_at', time.time())) < 3.0:
            is_bot = True
    except: pass

    if is_bot:
        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
            with conn: conn.execute("INSERT OR IGNORE INTO banned_ips (ip, reason) VALUES (?, ?)", (client_ip, "Honeypot or ToF triggered"))
        return jsonify(success=False, error="Invalid submission detected. IP Blocked.", banned=True, requested=False), 403

    # 2. In-Memory IP Rate Limiting (Max 5 submissions per hour)
    now_ts = time.time()
    with state_lock:
        if 'submit_rate_limits' not in state: state['submit_rate_limits'] = {}
        limit_data = state['submit_rate_limits'].get(client_ip, {"attempts": [], "blocked_until": 0})
        
        if now_ts < limit_data["blocked_until"]:
            return jsonify(success=False, error="Rate limited. Try again later."), 429
            
        attempts = [ts for ts in limit_data["attempts"] if now_ts - ts < 3600]
        if len(attempts) >= 5:
            limit_data["blocked_until"] = now_ts + 3600
            state['submit_rate_limits'][client_ip] = limit_data
            with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                with conn:
                    conn.execute("INSERT OR IGNORE INTO banned_ips (ip, reason) VALUES (?, ?)", (client_ip, "Rate limit exceeded (Trial and Error)"))
            return jsonify(success=False, error="Too many submissions. IP Blocked.", banned=True, requested=False), 403
            
        attempts.append(now_ts)
        limit_data["attempts"] = attempts
        state['submit_rate_limits'][client_ip] = limit_data

    # 3. Input Sanitization (Preventing XSS/Escape attacks)
    event_type = html.escape(data.get('event_type', ''))
    text = html.escape(data.get('text', ''))
    location = html.escape(data.get('location', ''))
    event_date = html.escape(data.get('event_date', ''))
    source_url = data.get('source_url', '').replace('<', '').replace('>', '') # Basic URL safety
    submitter_email = html.escape(data.get('submitter_email', ''))
    
    if not all([event_type, text, location, event_date, source_url, submitter_email]):
        return jsonify(success=False, error="Missing required fields"), 400
        
    try:
        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
            with conn:
                conn.execute("""
                    INSERT INTO user_submissions (event_type, text, location, event_date, source_url, submitter_email)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (event_type, text, location, event_date, source_url, submitter_email))
                
        send_alert_email("New BEACON Event Submission", f"A new {event_type} event was submitted by {submitter_email}.\n\nText: {text}\nDate: {event_date}\nLocation: {location}\nSource: {source_url}\n\nReview it in CoolAdmin.")
        return jsonify(success=True)
    except Exception as e:
        return jsonify(success=False, error=str(e)), 500

@app.route('/api/request_reinstatement', methods=['POST'])
def api_request_reinstatement():
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip()
    try:
        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
            with conn:
                conn.execute("UPDATE banned_ips SET reinstatement_requested = 1 WHERE ip=?", (client_ip,))
        return jsonify(success=True)
    except Exception as e:
        return jsonify(success=False, error=str(e))

@app.route('/api/internal/suggest_prompt', methods=['POST'])
def api_suggest_prompt():
    if not session.get("admin_auth") and session.get("role") != "Admin":
        return jsonify(success=False, error="Unauthorized"), 403
    
    with state_lock:
        if state.get("gemini_api_disabled", False):
            return jsonify(success=False, error="Gemini API is currently disabled due to quota exhaustion.")
            
    data = request.json
    topic = data.get('topic')
    bad_items = data.get('bad_items', [])
    
    if not topic or not bad_items:
        return jsonify(success=False, error="Missing data"), 400
        
    try:
        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
            c = conn.cursor()
            c.execute("SELECT prompt_text FROM prompts WHERE prompt_type = ? ORDER BY id DESC LIMIT 1", (topic,))
            row = c.fetchone()
            current_prompt = row[0] if row else "Default instruction for " + topic
    except:
        current_prompt = "Unknown"
        
    prompt = f"""
You are an expert prompt engineer. We use the following instructions to gather local data for Sault Ste. Marie, MI for the category "{topic}".
Current Prompt: "{current_prompt}"

However, it recently produced these bad or hallucinated items:
{json.dumps(bad_items)}

Please suggest a brief, improved version of the prompt that prevents these issues (e.g., by adding stricter constraints or specific exclusion rules).
Return ONLY the new prompt text. No markdown formatting, no explanations.
"""
    try:
        global gemini_client
        if not gemini_client: gemini_client = genai.Client(api_key=G_KEY)
        for m_id in get_best_models():
            try:
                resp = gemini_client.models.generate_content(model=m_id, contents=prompt)
                suggested = resp.text.strip()
                if suggested: return jsonify(success=True, suggested_prompt=suggested)
            except Exception as e:
                if handle_gemini_error(e):
                    return jsonify(success=False, error="Gemini API Quota Exhausted")
                continue
        return jsonify(success=False, error="AI generation failed")
    except Exception as e:
        return jsonify(success=False, error=str(e))

@app.route('/api/internal/api_health')
def api_health_endpoint():
    if not session.get("admin_auth") and session.get("role") != "Admin":
        return jsonify(success=False, error="Unauthorized"), 403
        
    health = {
        "openweathermap": {"status": "Unknown", "latency": "--", "warnings": []},
        "gemini": {"status": "Unknown", "latency": "--", "warnings": []}
    }
    
    # OpenWeatherMap
    try:
        start = time.time()
        res = http_session.get(f"https://api.openweathermap.org/data/2.5/weather?q=London&appid={OWM_KEY}", timeout=5)
        elapsed = round((time.time() - start) * 1000)
        health["openweathermap"]["latency"] = f"{elapsed}ms"
        if res.status_code == 200:
            health["openweathermap"]["status"] = "OK"
            if 'Warning' in res.headers: health["openweathermap"]["warnings"].append(res.headers['Warning'])
            if 'Deprecation' in res.headers: health["openweathermap"]["warnings"].append("Deprecation: " + res.headers['Deprecation'])
            if 'Sunset' in res.headers: health["openweathermap"]["warnings"].append("Sunset: " + res.headers['Sunset'])
        else:
            health["openweathermap"]["status"] = f"Error {res.status_code}"
            health["openweathermap"]["warnings"].append(res.text[:100])
    except Exception as e:
        health["openweathermap"]["status"] = "Offline"
        health["openweathermap"]["warnings"].append(str(e))
        
    # Gemini API
    try:
        start = time.time()
        global gemini_client
        if not gemini_client: gemini_client = genai.Client(api_key=G_KEY)
        
        m_list = list(gemini_client.models.list())
        elapsed = round((time.time() - start) * 1000)
        health["gemini"]["latency"] = f"{elapsed}ms"
        
        if m_list:
            health["gemini"]["status"] = "OK"
        else:
            health["gemini"]["status"] = "Error"
            health["gemini"]["warnings"].append("No models returned")
            
    except Exception as e:
        health["gemini"]["status"] = "Offline"
        health["gemini"]["warnings"].append(str(e))
        
    return jsonify(health)

@app.before_request
def check_disabled_pages():
    if request.path.startswith('/cooladmin') or request.path.startswith('/joeyadmin') or request.path.startswith('/admin') or request.path.startswith('/static') or request.path.startswith('/api/') or request.path.startswith('/docs') or request.path.startswith('/demo'):
        return
    with state_lock:
        disabled = state.get("disabled_pages", [])
    if request.path in disabled or (request.path != '/' and request.path.rstrip('/') in disabled):
        return "<body style='background:#010103; color:#00ffff; font-family:monospace; display:flex; flex-direction:column; justify-content:center; align-items:center; height:100vh; margin:0;'><h2>[ SYSTEM OFFLINE ]</h2><p style='color:#fff; opacity:0.5;'>This page has been temporarily disabled.</p></body>", 503

@app.route('/docs')
@app.route('/docs/')
@app.route('/docs/<path:filename>')
def serve_docs(filename='index'):
    if request.path == '/docs':
        return redirect('/docs/')
    if '..' in filename:
        return "Invalid path.", 400
    if filename.endswith('.html'):
        safe_name = secure_filename(filename)
        safe_path = os.path.join(BASE_DIR, 'docs', safe_name)
        if os.path.exists(safe_path):
            return send_from_directory(os.path.join(BASE_DIR, 'docs'), safe_name)
            
    safe_name = secure_filename(filename.replace('/', '_'))
    safe_path = os.path.join(BASE_DIR, 'docs', f"{safe_name}.md")
    if not os.path.exists(safe_path):
        if filename == 'index':
            if os.path.exists(os.path.join(BASE_DIR, 'docs', 'index.md')):
                safe_path = os.path.join(BASE_DIR, 'docs', 'index.md')
            else:
                return "Documentation not found.", 404
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

@app.route('/demo', methods=['GET', 'POST'])
def demo_hub():
    now_str = datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')
    active_demos = [p for p in get_beacon_pages() if p['slug'].startswith('demo-') and (not p.get('expires_at') or p['expires_at'] > now_str)]
    
    if request.method == 'POST':
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip()
        now_ts = time.time()
        
        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
            c = conn.cursor()
            c.execute("SELECT reinstatement_requested FROM banned_ips WHERE ip=?", (client_ip,))
            row = c.fetchone()
            if row:
                if row[0]: return "<script>alert('You have already requested reinstatement. Please wait.'); window.location.href='/demo';</script>"
                else: return "<script>if(confirm('Your IP is banned. Request reinstatement?')) { fetch('/api/request_reinstatement', {method:'POST'}).then(()=>alert('Requested.')); } window.location.href='/demo';</script>"

        zipcode = request.form.get('zipcode', '').strip()
        honeypot = request.form.get('website_url', '')
        rendered_at = request.form.get('rendered_at', 0)
        
        is_bot = False
        if honeypot: is_bot = True
        try:
            if time.time() - float(rendered_at) < 2.0: is_bot = True
        except: pass
        
        if is_bot:
            with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                with conn: conn.execute("INSERT OR IGNORE INTO banned_ips (ip, reason) VALUES (?, ?)", (client_ip, "Demo Honeypot/ToF"))
            return "<script>if(confirm('Invalid request. IP Banned. Request reinstatement?')) { fetch('/api/request_reinstatement', {method:'POST'}).then(()=>alert('Requested.')); } window.location.href='/demo';</script>"
        
        with state_lock:
            if 'demo_rate_limits' not in state:
                state['demo_rate_limits'] = {}
                
            limit_data = state['demo_rate_limits'].get(client_ip, {"attempts": [], "blocked_until": 0})
            
            if now_ts < limit_data["blocked_until"]:
                return "<script>alert('Too many attempts. Please wait 30 seconds before trying again.'); window.location.href='/demo';</script>"
            
            attempts = [ts for ts in limit_data["attempts"] if now_ts - ts < 30]
            if len(attempts) >= 3:
                limit_data["blocked_until"] = now_ts + 30
                state['demo_rate_limits'][client_ip] = limit_data
                with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                    with conn: conn.execute("INSERT OR IGNORE INTO banned_ips (ip, reason) VALUES (?, ?)", (client_ip, "Demo Rate Limit Exceeded"))
                return "<script>if(confirm('Too many attempts. IP Banned. Request reinstatement?')) { fetch('/api/request_reinstatement', {method:'POST'}).then(()=>alert('Requested.')); } window.location.href='/demo';</script>"
                
            attempts.append(now_ts)
            limit_data["attempts"] = attempts
            state['demo_rate_limits'][client_ip] = limit_data

        zipcode = request.form.get('zipcode', '').strip()
        if zipcode:
            slug = f"demo-{zipcode}"
            pages = get_beacon_pages()
            page = next((p for p in pages if p['slug'] == slug), None)
            
            if not page:
                if len(active_demos) >= 11:
                    return "<script>alert('Maximum active demos (11) reached. Please try again later once some expire.'); window.location.href='/demo';</script>"
                
                try:
                    w_res = requests.get(f"https://api.openweathermap.org/data/2.5/weather?q={zipcode},US&appid={OWM_KEY}", timeout=5).json()
                    if str(w_res.get("cod")) != "200" or not w_res.get("name"):
                        return f"<script>alert('Invalid zip code ({zipcode}). OpenWeather could not find a city for this location. Please try again.'); window.location.href='/demo';</script>"
                    title = w_res.get("name")
                except:
                    return "<script>alert('Error verifying zip code. Please try again later.'); window.location.href='/demo';</script>"

                expires = (datetime.now(TZ) + timedelta(hours=12)).strftime('%Y-%m-%d %H:%M:%S')
                try:
                    with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                        with conn:
                            conn.execute("INSERT INTO beacon_pages (slug, title, zipcode, expires_at) VALUES (?, ?, ?, ?)", (slug, title, zipcode, expires))
                        send_alert_email(f"New BEACON Demo: {zipcode}", f"A new demo environment has been provisioned.\n\nSlug: {slug}\nZipcode: {zipcode}\nExpires: {expires}\n\nCheck CoolAdmin for details.")
                except: pass
                
                with state_lock:
                    if 'tenants' not in state: state['tenants'] = {}
                    state['tenants'][slug] = {
                        "pulse": f"Welcome! Generating initial AI pulse for {title}...",
                        "main_config": {
                            "header": "BEACON DEMO",
                            "location": title.upper(),
                            "query": f"{zipcode},US"
                        },
                        "pulse_history": [
                            {"date": "EAP READY", "text": "Multicast listener attached. Try the Dispatch PWA to send a Lockdown alert!", "location": "System"},
                            {"date": "SIGNAGE READY", "text": "Go to /admin to inject Canva slides or YouTube loops.", "location": "System"},
                            {"date": "SMART CITY", "text": "AI is fetching local municipal alerts.", "location": "System"}
                        ],
                        "slides": [
                            {"id": "dashboard", "type": "dashboard", "duration": 15, "hidden": False},
                            {"id": "demo-text", "type": "text", "text": "This instance self-destructs in 12 hours.", "bg_color": "#000000", "text_color": "#00ffff", "duration": 5, "strobe": False}
                        ],
                        "is_demo": True,
                        "sync_status": "pending"
                    }
                    try:
                        with open(STATE_FILE, 'w') as sf: json.dump(state, sf)
                    except: pass
                
                def sync_and_ready():
                    sync_for_location(slug, title, f"{zipcode},US")
                    with state_lock:
                        if slug in state.get('tenants', {}):
                            state['tenants'][slug]['sync_status'] = "ready"
                            try:
                                with open(STATE_FILE, 'w') as sf: json.dump(state, sf)
                            except: pass
                
                threading.Thread(target=sync_and_ready, daemon=True).start()
            
            return redirect(f"/demo/loading/{slug}")
            
    return render_template('demo_hub.html', active_demos=active_demos)

@app.route('/demo/loading/<slug>')
def demo_loading(slug):
    with state_lock:
        status = state.get('tenants', {}).get(slug, {}).get('sync_status', 'ready')
    if status == 'ready':
        return redirect(f"/demo/{slug}")
    return f"""
    <body style='background:#050510; color:#00ffff; font-family:monospace; display:flex; flex-direction:column; justify-content:center; align-items:center; height:100vh; margin:0;'>
        <h2>[ PROVISIONING DEMO ENVIRONMENT ]</h2>
        <p style='color:#fff; opacity:0.8;'>Fetching AI models, building 3D assets, routing weather... please wait.</p>
        <div style='width: 50px; height: 50px; border: 5px solid #00ffff; border-top: 5px solid transparent; border-radius: 50%; animation: spin 1s linear infinite; margin-top: 20px;'></div>
        <style>@keyframes spin {{ 0% {{ transform: rotate(0deg); }} 100% {{ transform: rotate(360deg); }} }}</style>
        <script>
            setInterval(async () => {{
                try {{
                    const res = await fetch('/api/state/{slug}');
                    const data = await res.json();
                    if (data.sync_status === 'ready') window.location.href = '/demo/{slug}';
                }} catch(e) {{}}
            }}, 2000);
        </script>
    </body>
    """

@app.route('/demo/<slug>')
def demo_viewer(slug):
    pages = get_beacon_pages()
    page = next((p for p in pages if p['slug'] == slug), None)
    if not page: return "Demo not found", 404
    
    try:
        expires_str = page.get('expires_at')
        if expires_str:
            dt_aware = TZ.localize(datetime.strptime(expires_str, '%Y-%m-%d %H:%M:%S'))
            expires_ts = dt_aware.timestamp() * 1000
        else: expires_ts = 0
    except: expires_ts = 0
        
    return render_template('demo_viewer.html', slug=slug, expires_ts=expires_ts)

@app.route('/demo/<slug>/docs')
def demo_docs(slug):
    pages = get_beacon_pages()
    page = next((p for p in pages if p['slug'] == slug), None)
    if not page: return "Demo not found", 404
    
    docs_list = []
    try:
        docs_dir = os.path.join(BASE_DIR, 'docs')
        if os.path.exists(docs_dir):
            for f in sorted(os.listdir(docs_dir)):
                if f == 'index.md': continue
                if f.endswith('.md'):
                    docs_list.append({"name": f.replace('.md', '').replace('_', ' ').title(), "url": f"/docs/{f.replace('.md', '')}"})
                elif f.endswith('.html'):
                    docs_list.append({"name": f.replace('.html', '').replace('_', ' ').title(), "url": f"/docs/{f}"})
    except: pass
    
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Demo Docs - {slug}</title>
        <style>
            body {{ background: #050510; color: #00ffff; font-family: monospace; padding: 20px; }}
            h2 {{ border-bottom: 1px solid #00ffff; padding-bottom: 10px; margin-top: 0; }}
            a.doc-link {{ color: #fff; text-decoration: none; display: block; padding: 15px; background: rgba(0,255,255,0.05); margin-bottom: 10px; border: 1px solid rgba(0,255,255,0.2); border-radius: 8px; transition: 0.2s; font-size: 1.1rem; }}
            a.doc-link:hover {{ background: rgba(0,255,255,0.2); box-shadow: 0 0 15px #00ffff; transform: translateX(5px); }}
        </style>
    </head>
    <body>
        <h2>📚 BEACON Documentation Hub</h2>
        <p style="color: #aaa; font-size: 1.1rem; margin-bottom: 20px;">Select a document below to view its contents.</p>
    """
    for doc in docs_list:
        html += f"<a href='{doc['url']}' target='_blank' class='doc-link'>📄 {doc['name']}</a>\\n"
    
    html += "</body></html>"
    return html

@app.route('/demo/<slug>/dashboard')
def demo_dashboard(slug):
    pages = get_beacon_pages()
    page = next((p for p in pages if p['slug'] == slug), None)
    if not page: return "Demo not found", 404
    
    with state_lock: 
        page_state = state.copy()
        page_state.update(state.get('tenants', {}).get(slug, {}))
        page_state['page_title'] = page['title']
        page_state['page_slug'] = slug
        page_state['is_demo'] = True
        return render_template('index.html', build_timestamp=os.environ.get("BUILD_TIMESTAMP", "Local Dev"), is_car_display=False, **page_state)

@app.route('/demo/<slug>/school')
def demo_school(slug):
    pages = get_beacon_pages()
    page = next((p for p in pages if p['slug'] == slug), None)
    if not page: return "Demo not found", 404
    
    with state_lock: 
        page_state = state.copy()
        page_state.update(state.get('tenants', {}).get(slug, {}))
        page_state['page_title'] = page['title']
        page_state['page_slug'] = slug
        page_state['is_demo'] = True
        page_state['school_alerts'] = state.get('school_alerts', {})
        page_state['school_closings'] = state.get('school_closings', {})
        return render_template('school_dashboard.html', **page_state)

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
@app.route('/login', methods=['GET', 'POST'])
def login_page():
    error_msg = ""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        next_url = request.form.get('next') or '/cooladmin'
        
        if username == admin_username and password == admin_password:
            session['admin_auth'] = True
            session['role'] = 'Admin' # Local admin bypasses RBAC globally
            return redirect(next_url)
            
        try:
            with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                c = conn.cursor()
                c.execute("SELECT role, password, last_login FROM rbac_users WHERE username=? AND provider='Local'", (username,))
                row = c.fetchone()
                if row and row[1] and check_password_hash(row[1], password):
                    session['user'] = username
                    session['role'] = row[0]
                    if row[0] == 'Admin':
                        session['admin_auth'] = True
                        
                    last_login = row[2]
                    now_str = datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')
                    try:
                        with conn:
                            conn.execute("UPDATE rbac_users SET last_login=? WHERE username=? AND provider='Local'", (now_str, username))
                    except: pass
                    
                    if not last_login:
                        send_alert_email("First Time Login", f"User {username} logged in for the first time.", os.environ.get("SMTP_USER", "joseph@morrowedge.com"))
                    else:
                        ll_dt = datetime.strptime(last_login, '%Y-%m-%d %H:%M:%S')
                        if (datetime.now(TZ).replace(tzinfo=None) - ll_dt).days > 30:
                            send_alert_email("Re-engagement Login", f"User {username} logged in after more than 30 days.", os.environ.get("SMTP_USER", "joseph@morrowedge.com"))
                            
                    return redirect(next_url)
        except Exception as e:
            print(f"Login error: {e}", flush=True)
            
        error_msg = "<p style='color:#ff4444; text-align:center; font-weight:bold;'>Invalid credentials</p>"

    next_url = request.args.get('next', '')
    try:
        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
            c = conn.cursor()
            c.execute("SELECT provider FROM sso_configs WHERE enabled=1")
            ssos = [r[0] for r in c.fetchall()]
    except: ssos = []
    
    buttons = "".join([f"<a href='/login/{p.lower()}' style='display:block; padding:10px; background:#00ffff; color:#000; text-decoration:none; text-align:center; margin-bottom:10px; border-radius:4px; font-weight:bold;'>Login with {p}</a>" for p in ssos])
    
    native_form = f"""
    <form method="POST" action="/login" style='margin-top:20px; text-align:left;'>
        <input type="hidden" name="next" value="{next_url}">
        <input type="text" name="username" placeholder="Username" style="width:100%; padding:10px; margin-bottom:10px; border:1px solid #00ffff; background:#000; color:#0ff; border-radius:4px; box-sizing:border-box;">
        <input type="password" name="password" placeholder="Password" style="width:100%; padding:10px; margin-bottom:15px; border:1px solid #00ffff; background:#000; color:#0ff; border-radius:4px; box-sizing:border-box;">
        <button type="submit" style="width:100%; padding:10px; background:transparent; border:1px solid #00ffff; color:#00ffff; border-radius:4px; font-weight:bold; cursor:pointer;">Native Login</button>
        <div style="text-align:right; margin-top:5px;"><a href="/forgot_password" style="color:#aaa; font-size:0.8rem; text-decoration:none;">Forgot Password?</a></div>
    </form>
    """
    return f"<body style='background:#050510; color:#00ffff; font-family:monospace; display:flex; justify-content:center; align-items:center; height:100vh; margin:0;'><div style='background:rgba(0,50,50,0.2); border:1px solid #00ffff; padding:30px; border-radius:8px; box-shadow:0 0 15px rgba(0,255,255,0.1); width:300px;'><h2 style='text-align:center; margin-top:0;'>BEACON LOGIN</h2>{error_msg}{buttons}{native_form}</div></body>"

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email')
        if email:
            send_alert_email("Password Reset Requested", f"User {email} has requested a password reset. Please contact them.", os.environ.get("SMTP_USER", "joseph@morrowedge.com"))
        return "<body style='background:#050510; color:#00ffff; font-family:monospace; display:flex; justify-content:center; align-items:center; height:100vh; margin:0;'><h3>If the email exists, a reset instruction has been sent to the administrator.</h3></body>"
    return "<body style='background:#050510; color:#00ffff; font-family:monospace; display:flex; justify-content:center; align-items:center; height:100vh; margin:0;'><form method='POST' style='background:rgba(0,50,50,0.2); border:1px solid #00ffff; padding:30px; border-radius:8px;'><h3 style='margin-top:0;'>Reset Password</h3><input type='email' name='email' placeholder='Enter your Account Email' required style='padding:10px; width:100%; box-sizing:border-box; margin-bottom:10px; background:#000; color:#0ff; border:1px solid #0ff;'/><button type='submit' style='padding:10px; width:100%; background:#00ffff; color:#000; font-weight:bold; border:none; cursor:pointer;'>Request Reset</button></form></body>"

@app.route('/profile/delete_account', methods=['POST'])
def delete_account():
    user = session.get('user')
    if user and session.get('provider') == 'Local':
        try:
            with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                with conn:
                    conn.execute("DELETE FROM rbac_users WHERE username=?", (user,))
            send_alert_email("Account Deleted", f"User {user} has deleted their native account.", os.environ.get("SMTP_USER", "joseph@morrowedge.com"))
        except: pass
        session.clear()
    return redirect('/')

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
    ua = request.headers.get('User-Agent', '').lower()
    # Detect Fermata Auto, general WebView, or a manual ?auto=true parameter
    is_car = 'fermata' in ua or 'wv' in ua or request.args.get('auto') == 'true'
    with state_lock: 
        return render_template('index.html', build_timestamp=os.environ.get("BUILD_TIMESTAMP", "Local Dev"), is_car_display=is_car, **state.copy())

@app.route('/auto')
def auto_display():
    # Dedicated route to easily bookmark in the car without relying on User-Agent
    with state_lock: 
        return render_template('index.html', build_timestamp=os.environ.get("BUILD_TIMESTAMP", "Local Dev"), is_car_display=True, **state.copy())

@app.route('/index')
def index_redirect():
    return redirect('/')

@app.route('/sault-weather')
def sault_weather_redirect():
    return redirect('/')

@app.route('/sault-schools')
@app.route('/sault_schools')
def sault_schools_redirect():
    return redirect('/schools/sault-schools')

@app.route('/pickford-schools')
@app.route('/pickford_schools')
def pickford_schools_redirect():
    return redirect('/schools/pickford-schools')

@app.route('/schools/<slug>')
def dynamic_school(slug):
    pages = get_beacon_pages()
    page = next((p for p in pages if p['slug'] == slug), None)
    if not page:
        return "Page not found", 404
    
    with state_lock: 
        page_state = state.copy()
        page_state.update(state.get('tenants', {}).get(slug, {}))
        page_state['page_title'] = page['title']
        page_state['page_slug'] = slug
        page_state['school_alerts'] = state.get('school_alerts', {})
        page_state['school_closings'] = state.get('school_closings', {})
        return render_template('school_dashboard.html', **page_state)

@app.route('/cooladmin', methods=['GET', 'POST'], strict_slashes=False)
@app.route('/joeyadmin', methods=['GET', 'POST'], strict_slashes=False) # Legacy support
def cooladmin():
    auth = request.authorization
        
    is_basic = auth and auth.username == admin_username and auth.password == admin_password
    is_native = session.get("admin_auth") is True
    is_sso = session.get("role") == "Admin"
    
    if not (is_basic or is_native or is_sso):
        return redirect('/login')

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

        elif action == 'unban_ip':
            ip = request.form.get('ip')
            try:
                with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                    with conn: conn.execute("DELETE FROM banned_ips WHERE ip=?", (ip,))
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
            provider = request.form.get('provider', 'Local')
            role = request.form.get('role', 'Viewer')
            password = request.form.get('password', '')
            
            if provider == 'Local' and '@' not in username:
                username = f"{username}@local.invalid" # Enforce email-like struct natively
            
            hashed_pw = generate_password_hash(password) if password else None
            
            if username:
                try:
                    with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                        with conn:
                            if hashed_pw:
                                conn.execute("INSERT OR REPLACE INTO rbac_users (username, role, provider, type, override_group, password) VALUES (?, ?, ?, ?, ?, ?)", 
                                             (username, role, provider, rtype, override_group, hashed_pw))
                            else:
                                conn.execute("INSERT OR IGNORE INTO rbac_users (username, role, provider, type, override_group) VALUES (?, ?, ?, ?, ?)", 
                                             (username, role, provider, rtype, override_group))
                                conn.execute("UPDATE rbac_users SET role=?, provider=?, type=?, override_group=? WHERE username=?",
                                             (role, provider, rtype, override_group, username))
                except Exception as e:
                    print(e, flush=True)
            return redirect('/cooladmin')
        elif action == 'delete_rbac_user':
            with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                with conn:
                    conn.execute("DELETE FROM rbac_users WHERE id = ?", (request.form.get('user_id'),))
            return redirect('/cooladmin')

        elif action == 'delete_garage_sale':
            sale_id = request.form.get('sale_id')
            try:
                with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                    with conn:
                        real_id = sale_id.replace('sale_', '') if 'sale_' in str(sale_id) else sale_id
                        conn.execute("DELETE FROM garage_sales WHERE id = ?", (real_id,))
            except: pass
            return redirect('/cooladmin')
            
        elif action == 'delete_sault_tribe':
            tribe_id = request.form.get('tribe_id')
            try:
                with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                    with conn:
                        real_id = tribe_id.replace('tribe_', '') if 'tribe_' in str(tribe_id) else tribe_id
                        conn.execute("DELETE FROM sault_tribe WHERE id = ?", (real_id,))
            except: pass
            return redirect('/cooladmin')

        elif action == 'add_vetted_source':
            topic = request.form.get('topic')
            name = request.form.get('name')
            url = request.form.get('url')
            if topic and url:
                try:
                    with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                        with conn: conn.execute("INSERT INTO vetted_sources (topic, source_name, source_url) VALUES (?, ?, ?)", (topic, name, url))
                except: pass
            return redirect('/cooladmin')
            
        elif action == 'delete_vetted_source':
            source_id = request.form.get('source_id')
            delete_archives = request.form.get('delete_archives') == 'yes'
            try:
                with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                    c = conn.cursor()
                    c.execute("SELECT topic, source_url FROM vetted_sources WHERE id=?", (source_id,))
                    row = c.fetchone()
                    if row:
                        topic, url = row
                        if delete_archives and topic in ['pulses', 'garage_sales', 'sault_tribe', 'sault_schools']:
                            c.execute(f"SELECT id, details FROM {topic}")
                            for r in c.fetchall():
                                try:
                                    det = json.loads(r[1]) if r[1] else {}
                                    srcs = det.get('sources', [])
                                    if len(srcs) == 1 and srcs[0].get('url') == url:
                                        conn.execute(f"DELETE FROM {topic} WHERE id=?", (r[0],))
                                except: pass
                    with conn: conn.execute("DELETE FROM vetted_sources WHERE id=?", (source_id,))
            except: pass
            return redirect('/cooladmin')

        elif action == 'add_scheduled_source':
            name = request.form.get('name')
            target_table = request.form.get('target_table')
            scrape_url = request.form.get('scrape_url')
            schedule_type = request.form.get('schedule_type')
            schedule_details = request.form.get('schedule_details')
            prompt_text = request.form.get('prompt_text')
            if name and target_table and scrape_url:
                try:
                    with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                        with conn:
                            conn.execute("INSERT INTO scheduled_sources (name, target_table, scrape_url, schedule_type, schedule_details, prompt_text) VALUES (?, ?, ?, ?, ?, ?)", (name, target_table, scrape_url, schedule_type, schedule_details, prompt_text))
                except Exception as e: print(f"Error adding scheduled source: {e}", flush=True)
            return redirect('/cooladmin')
        elif action == 'delete_scheduled_source':
            try:
                with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                    with conn: conn.execute("DELETE FROM scheduled_sources WHERE id=?", (request.form.get('source_id'),))
            except: pass
            return redirect('/cooladmin')
            
        elif action == 'toggle_scheduled_source':
            try:
                with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                    with conn: conn.execute("UPDATE scheduled_sources SET is_active = NOT is_active WHERE id=?", (request.form.get('source_id'),))
            except: pass
            return redirect('/cooladmin')

        elif action == 'edit_details':
            item_id = request.form.get('item_id')
            table = request.form.get('table')
            real_id = re.sub(r'^[a-z]+_', '', str(item_id)) if item_id else None
            details = { "who": request.form.get('who', ''), "what": request.form.get('what', ''), "where": request.form.get('where', ''), "when": request.form.get('when', ''), "why": request.form.get('why', ''), "sources": [] }
            try:
                if request.form.get('sources'): details['sources'] = json.loads(request.form.get('sources'))
            except: pass
            if table in ['pulses', 'garage_sales', 'sault_tribe', 'sault_schools']:
                try:
                    with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                        with conn:
                            conn.execute("INSERT INTO ai_training_log (topic, action_type, new_details) VALUES (?, ?, ?)", (table, "manual_edit", json.dumps(details)))
                            conn.execute(f"UPDATE {table} SET details = ? WHERE id = ?", (json.dumps(details), real_id))  # nosec B608
                except Exception as e: print(e, flush=True)
            return redirect('/cooladmin')

        elif action == 'refresh_details':
            item_id = request.form.get('item_id')
            table = request.form.get('table')
            text = request.form.get('text')
            real_id = re.sub(r'^[a-z]+_', '', str(item_id)) if item_id else None
            if table in ['pulses', 'garage_sales', 'sault_tribe', 'sault_schools']:
                v_res, p_used = verify_events_batch([{"id": real_id, "text": text}])
                if real_id in v_res:
                    res = v_res[real_id]
                    new_details = res.get("details", {})
                    if not res.get("hallucinated"):
                        try:
                            with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                                with conn:
                                    conn.execute("INSERT INTO ai_training_log (topic, original_text, new_details, action_type, gather_prompt) VALUES (?, ?, ?, ?, ?)", (table, text, json.dumps(new_details), "refresh_single", p_used))
                                    conn.execute(f"UPDATE {table} SET details = ? WHERE id = ?", (json.dumps(new_details), real_id))  # nosec B608
                        except Exception as e: print(e, flush=True)
            return redirect('/cooladmin')

        elif action == 'refresh_tower':
            table = request.form.get('table')
            if table in ['pulses', 'garage_sales', 'sault_tribe', 'sault_schools']:
                try:
                    with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                        c = conn.cursor()
                        c.execute(f"SELECT id, text FROM {table} ORDER BY id DESC LIMIT 20")  # nosec B608
                        rows = c.fetchall()
                    events_to_verify = [{"id": str(r[0]), "text": r[1]} for r in rows]
                    v_res, p_used = verify_events_batch(events_to_verify)
                    with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                        with conn:
                            for row in rows:
                                i_id = str(row[0])
                                if i_id in v_res and not v_res[i_id].get("hallucinated"):
                                    conn.execute(f"UPDATE {table} SET details = ? WHERE id = ?", (json.dumps(v_res[i_id].get("details", {})), i_id))  # nosec B608
                            conn.execute("INSERT INTO ai_training_log (topic, action_type, gather_prompt) VALUES (?, ?, ?)", (table, "refresh_tower", p_used))
                except Exception as e: print(e, flush=True)
            return redirect('/cooladmin')

        elif action in ['add_pulse', 'add_garage_sale', 'add_sault_tribe', 'add_sault_school_event']:
            table_map = {
                'add_pulse': 'pulses', 'add_garage_sale': 'garage_sales',
                'add_sault_tribe': 'sault_tribe', 'add_sault_school_event': 'sault_schools'
            }
            table = table_map[action]
            text = request.form.get('text', '').strip()
            date_input = request.form.get('date', '').strip()
            loc = request.form.get('location', '').strip()
            source_url = request.form.get('source_url', '').strip()
            save_source = request.form.get('save_source') == 'yes'
            
            if text:
                now = datetime.now(TZ)
                if date_input:
                    try:
                        dt = datetime.strptime(date_input, '%Y-%m-%dT%H:%M')
                        if dt.year < 2000 or dt.year > 2100: raise ValueError("Year out of bounds")
                        dt = TZ.localize(dt)
                    except Exception as e:
                        if request.headers.get('Accept') == 'application/json': return jsonify(success=False, error="Invalid date/time provided.")
                        return f"Invalid date/time provided: {str(e)}", 400
                else:
                    dt = now
                date_str = dt.strftime('%B %d, %I:%M %p') + " [manual]"
                
                if save_source and source_url:
                    try:
                        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                            with conn: conn.execute("INSERT INTO vetted_sources (topic, source_name, source_url) VALUES (?, ?, ?)", (table, 'Manual Custom Source', source_url))
                    except: pass

                details = {}
                try:
                    v_res, _ = verify_events_batch([{"id": "manual", "text": text}], is_manual=True)
                    if "manual" in v_res:
                        details = v_res["manual"].get("details", {})
                        details["model"] = "manual"
                        details["timestamp"] = now.strftime('%Y-%m-%d %I:%M %p')
                        if source_url:
                            details["sources"] = [{"url": source_url, "title": "Manual Submission Source"}]
                            details["source_saved"] = save_source
                except Exception as e:
                    print(f"Manual 5W extraction failed: {e}", flush=True)

                try:
                    with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                        with conn: 
                            conn.execute(f"INSERT INTO {table} (date, text, location, details) VALUES (?, ?, ?, ?)", (date_str, text, loc, json.dumps(details)))  # nosec B608
                except: pass

                with state_lock:
                    if table == 'pulses':
                        state['pulse_history'] = load_history()
                    elif table == 'garage_sales':
                        state['garage_sales'] = load_garage_sales()
                    elif table == 'sault_tribe':
                        state['sault_tribe'] = load_sault_tribe()
                    elif table == 'sault_schools':
                        state['sault_schools'] = load_sault_schools()
                    try:
                        with open(STATE_FILE, 'w') as sf: json.dump(state, sf)
                    except: pass
                    
                if request.headers.get('Accept') == 'application/json':
                    return jsonify(success=True)

            return redirect('/cooladmin')

        elif action == 'approve_submission':
            sub_id = request.form.get('sub_id')
            notify = request.form.get('notify_user') == 'yes'
            try:
                with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                    c = conn.cursor()
                    c.execute("SELECT event_type, text, location, event_date, source_url, submitter_email FROM user_submissions WHERE id=?", (sub_id,))
                    row = c.fetchone()
                    if row:
                        event_type, text, location, event_date, source_url, submitter_email = row
                        try:
                            dt = datetime.strptime(event_date, '%Y-%m-%dT%H:%M')
                            date_str = dt.strftime('%B %d')
                        except:
                            date_str = event_date
                        details = {
                            "sources": [{"url": source_url, "title": "User Submitted Source"}],
                            "submitter_email": submitter_email
                        }
                        if event_type in ['pulses', 'garage_sales', 'sault_tribe', 'sault_schools']:
                            with conn:
                                conn.execute(f"INSERT INTO {event_type} (date, text, location, details) VALUES (?, ?, ?, ?)", (date_str, text, location, json.dumps(details)))  # nosec B608
                                conn.execute("UPDATE user_submissions SET status='approved' WHERE id=?", (sub_id,))
                        if notify and submitter_email:
                            send_alert_email("BEACON Event Approved", f"Hi!\n\nYour {event_type} submission ('{text}') has been approved and added to the dashboard.", submitter_email)
            except Exception as e: print(e, flush=True)
            return redirect('/cooladmin')
            
        elif action == 'reject_submission':
            sub_id = request.form.get('sub_id')
            notify = request.form.get('notify_user') == 'yes'
            try:
                with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                    c = conn.cursor()
                    c.execute("SELECT event_type, text, submitter_email FROM user_submissions WHERE id=?", (sub_id,))
                    row = c.fetchone()
                    if row:
                        event_type, text, submitter_email = row
                        with conn:
                            conn.execute("UPDATE user_submissions SET status='rejected' WHERE id=?", (sub_id,))
                        if notify and submitter_email:
                            send_alert_email("BEACON Event Update", f"Hi!\n\nUnfortunately, your {event_type} submission ('{text}') could not be verified or was rejected by our moderation team.", submitter_email)
            except: pass
            return redirect('/cooladmin')

        elif action == 'delete_sault_school_event':
            event_id = request.form.get('event_id')
            try:
                with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                    with conn:
                        real_id = event_id.replace('school_', '') if 'school_' in str(event_id) else event_id
                        conn.execute("DELETE FROM sault_schools WHERE id = ?", (real_id,))
            except: pass
            return redirect('/cooladmin')
            
        elif action == 'add_prompt':
            prompt_type = request.form.get('prompt_type')
            prompt_text = request.form.get('prompt_text', '').strip()
            if prompt_type and prompt_text:
                try:
                    with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                        with conn: conn.execute("INSERT INTO prompts (prompt_type, prompt_text) VALUES (?, ?)", (prompt_type, prompt_text))
                except: pass
            return redirect('/cooladmin')

        elif action == 'delete_responses':
            target = request.form.get('target')
            try:
                with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                    with conn:
                        conn.execute("DELETE FROM prompts WHERE prompt_type = ? AND is_default = 0 AND id NOT IN (SELECT id FROM prompts WHERE prompt_type = ? ORDER BY id DESC LIMIT 1)", (target, target))
            except: pass
            return redirect('/cooladmin')

        elif action == 'delete_tower':
            target = request.form.get('target')
            try:
                with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                    with conn:
                        conn.execute("DELETE FROM prompts WHERE prompt_type = ? AND is_default = 0", (target,))
            except: pass
            return redirect('/cooladmin')

        elif action == 'shutdown':
            confirm_user = request.form.get('username')
            confirm_pass = request.form.get('password')
            if confirm_user == admin_username and confirm_pass == admin_password:
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
                
        elif action == 'reenable_gemini':
            with state_lock:
                state["gemini_api_disabled"] = False
                try:
                    with open(STATE_FILE, 'w') as sf: json.dump(state, sf)
                except: pass
            log_system_event("GEMINI_API_ENABLED", "Admin manually re-enabled Gemini API")
            return redirect('/cooladmin')

        elif action == 'refresh_missing_details':
            def run_backfill():
                tables = ['pulses', 'old_pulses', 'garage_sales', 'sault_tribe', 'sault_schools']
                with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                    for table in tables:
                        try:
                            c = conn.cursor()
                            c.execute(f"SELECT id, text FROM {table} WHERE details = '{{}}' OR details IS NULL OR details = ''")
                            rows = c.fetchall()
                            batch = []
                            for r in rows:
                                batch.append({"id": str(r[0]), "text": r[1]})
                                if len(batch) >= 10:
                                    v_res, _ = verify_events_batch(batch, is_manual=True)
                                    for item in batch:
                                        i_id = item["id"]
                                        if i_id in v_res:
                                            new_details = v_res[i_id].get("details", {})
                                            c.execute(f"UPDATE {table} SET details = ? WHERE id = ?", (json.dumps(new_details), int(i_id)))
                                    conn.commit()
                                    batch = []
                                    print(f"[BACKFILL] Processed batch for {table}...", flush=True)
                                    time.sleep(5)  # 5-second sleep to safely respect API quotas
                            if batch:
                                v_res, _ = verify_events_batch(batch, is_manual=True)
                                for item in batch:
                                    i_id = item["id"]
                                    if i_id in v_res:
                                        new_details = v_res[i_id].get("details", {})
                                        c.execute(f"UPDATE {table} SET details = ? WHERE id = ?", (json.dumps(new_details), int(i_id)))
                                conn.commit()
                                print(f"[BACKFILL] Finished {table}.", flush=True)
                        except Exception as e:
                            print(f"[BACKFILL ERROR] {table}: {e}", flush=True)
                print("[BACKFILL] All tables completed.", flush=True)
            
            threading.Thread(target=run_backfill, daemon=True).start()
            return redirect('/cooladmin')
        
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
        current_pulse = state.get('pulse') or ''
        pulse_history = state.get('pulse_history') or []
        disabled_pages = state.get('disabled_pages') or []
        eap_pin = state.get('eap_pin', '123456')
        agenda_votes = state.get('agenda_votes', {})
        gemini_api_disabled = state.get('gemini_api_disabled', False)
    vetted_sources = get_vetted_sources()

    try:
        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
            c = conn.cursor()
            c.execute("SELECT id, submitted_at, event_type, text, location, event_date, source_url, submitter_email FROM user_submissions WHERE status='pending' ORDER BY id ASC")
            pending_submissions = [{"id": r[0], "submitted_at": r[1], "event_type": r[2], "text": r[3], "location": r[4], "event_date": r[5], "source_url": r[6], "submitter_email": r[7]} for r in c.fetchall()]
    except:
        pending_submissions = []

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
            c.execute("SELECT ip, reason, reinstatement_requested, banned_at FROM banned_ips ORDER BY banned_at DESC")
            banned_ips = [{"ip": r[0], "reason": r[1], "requested": bool(r[2]), "banned_at": r[3]} for r in c.fetchall()]
    except:
        banned_ips = []

    try:
        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
            c = conn.cursor()
            c.execute("SELECT id, retrieved_at, date, text, reason FROM hallucinations_log ORDER BY retrieved_at DESC")
            hallucinations = [{"id": r[0], "retrieved_at": r[1], "date": r[2], "text": r[3], "reason": r[4]} for r in c.fetchall()]
    except:
        hallucinations = []
        
    try:
        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
            c = conn.cursor()
            c.execute("SELECT id, prompt_type, prompt_text, is_default FROM prompts ORDER BY id DESC")
            active_prompts = []
            seen_types = set()
            for r in c.fetchall():
                if r[1] not in seen_types:
                    active_prompts.append({"id": r[0], "prompt_type": r[1], "prompt_text": r[2], "is_default": bool(r[3])})
                    seen_types.add(r[1])
    except:
        active_prompts = []
        
    try:
        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
            c = conn.cursor()
            c.execute("SELECT id, name, target_table, scrape_url, schedule_type, schedule_details, prompt_text, last_run, is_active FROM scheduled_sources ORDER BY id DESC")
            scheduled_sources = [{"id": r[0], "name": r[1], "target_table": r[2], "scrape_url": r[3], "schedule_type": r[4], "schedule_details": r[5], "prompt_text": r[6], "last_run": r[7], "is_active": bool(r[8])} for r in c.fetchall()]
    except: scheduled_sources = []

    garage_sales = load_garage_sales() or []
    sault_tribe = load_sault_tribe() or []
    sault_schools = load_sault_schools() or []
        
    known_routes = {
        "/": "Main Dashboard",
        "/sault-schools": "Sault Schools",
        "/pickford-schools": "Pickford Schools",
        "/rpg": "3D Sandbox (Buddy's World)",
        "/dispatch": "EAP Dispatch (PWA)",
        "/docs": "Documentation Hub",
        "/admin": "Admin Dashboard",
        "/demo": "Interactive Demo Hub",
        "/cooladmin": "Super Admin (CoolAdmin)",
        "/login": "SSO Login"
    }
    
    site_hierarchy = {
        "HTML Pages": [],
        "Dynamic Pages": [],
        "Routes": [],
        "Docs": []
    }
    added_urls = set()
    
    for url, name in known_routes.items():
        if url.startswith('/docs'):
            site_hierarchy["Docs"].append({"name": name, "url": url})
        else:
            site_hierarchy["HTML Pages"].append({"name": name, "url": url})
        added_urls.add(url)
        
    for rule in app.url_map.iter_rules():
        if 'GET' in rule.methods and not rule.arguments:
            url = str(rule)
            if url not in added_urls and not url.startswith('/api/') and not url.startswith('/static/'):
                if url.endswith('/') and url[:-1] in added_urls: continue
                
                if url.startswith('/docs/'):
                    site_hierarchy["Docs"].append({"name": f"Route: {url}", "url": url})
                else:
                    site_hierarchy["Routes"].append({"name": f"Route: {url}", "url": url})
                added_urls.add(url)
                
    try:
        docs_dir = os.path.join(BASE_DIR, 'docs')
        if os.path.exists(docs_dir):
            for f in sorted(os.listdir(docs_dir)):
                if f == 'index.md': continue
                if f.endswith('.md'):
                    doc_url = f"/docs/{f.replace('.md', '')}"
                    if doc_url not in added_urls:
                        site_hierarchy["Docs"].append({"name": f"Doc: {f.replace('.md', '').replace('_', ' ').title()}", "url": doc_url})
                        added_urls.add(doc_url)
                elif f.endswith('.html'):
                    doc_url = f"/docs/{f}"
                    if doc_url not in added_urls:
                        site_hierarchy["Docs"].append({"name": f"Doc Page: {f.replace('.html', '').replace('_', ' ').title()}", "url": doc_url})
                        added_urls.add(doc_url)
    except: pass
    
    portfolio_url = "/portfolio"
    if portfolio_url not in added_urls:
        site_hierarchy["HTML Pages"].append({"name": "Developer Portfolio", "url": portfolio_url})

    for page in get_beacon_pages():
        url = f"/schools/{page['slug']}"
        if url not in added_urls:
            site_hierarchy["Dynamic Pages"].append({"name": f"{page['title']} (Custom)", "url": url})
            added_urls.add(url)

    return render_template('joeyadmin.html', services=service_status, metrics=metrics, beacon_pages=get_beacon_pages(), eap_subs=get_eap_subscriptions(), current_pulse=current_pulse, pulse_history=pulse_history, disabled_pages=disabled_pages, hallucinations=hallucinations, cleanup_summary=cleanup_summary, site_hierarchy=site_hierarchy, sso_configs=sso_configs, rbac_users=rbac_users, eap_pin=eap_pin, garage_sales=garage_sales, sault_tribe=sault_tribe, sault_schools=sault_schools, agenda_votes=agenda_votes, pending_submissions=pending_submissions, banned_ips=banned_ips, active_prompts=active_prompts, vetted_sources=vetted_sources, scheduled_sources=scheduled_sources, gemini_api_disabled=gemini_api_disabled)

@app.route('/portfolio')
def portfolio():
    return render_template('portfolio.html')

@app.route('/admin', methods=['GET', 'POST'], strict_slashes=False)
@app.route('/<slug>/admin', methods=['GET', 'POST'], strict_slashes=False)
@app.route('/schools/<slug>/admin', methods=['GET', 'POST'], strict_slashes=False)
@app.route('/demo/<slug>/admin', methods=['GET', 'POST'], strict_slashes=False)
def admin(slug="main"):
    if slug == 'schools':
        return redirect('/admin')
        
    is_sso_editor = session.get("role") in ["Admin", "Editor", "Sales"]
    is_native_auth = session.get("admin_auth") is True
    
    if not slug.startswith('demo-') and not (is_sso_editor or is_native_auth):
        return redirect(url_for('login_page', next=request.path))
        
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'login':
            return redirect(request.path)
            
        audit_details = request.form.to_dict()
        if 'password' in audit_details: audit_details['password'] = '***'
        user_id = session.get("user") if session.get("user") else "local_admin"
        log_audit_event(user_id, action, audit_details)
        
        with state_lock:
            if slug == "main":
                target_state = state
            else:
                if 'tenants' not in state: state['tenants'] = {}
                if slug not in state['tenants']: state['tenants'][slug] = {}
                target_state = state['tenants'][slug]

            if action in ['add_text_slide', 'add_image_slide', 'add_video_slide', 'add_iframe_slide', 'move_slide_up', 'move_slide_down', 'toggle_slide', 'delete_slide', 'update_dashboard_slide']:
                if slug not in slide_history:
                    slide_history[slug] = {'undo': [], 'redo': []}
                slide_history[slug]['undo'].append(copy.deepcopy(target_state.get('slides', [])))
                slide_history[slug]['redo'].clear()

            if action == 'update_flare':
                if request.form.get('flare_active') == 'yes':
                    target_state['emergency'] = {"active": True, "message": request.form.get('message', ''), "color": request.form.get('color', '#ff0000')}
                else:
                    if 'emergency' in target_state: target_state['emergency']['active'] = False
            elif action == 'update_branding':
                target_state['branding'] = {
                    "text": request.form.get('branding_text', '').strip(),
                    "color": request.form.get('branding_color', '#00ffff')
                }
            elif action == 'update_main_config':
                target_state['main_config'] = {
                    "header": request.form.get('header_text', 'MORROW EDGE | BEACON Buddy').strip(),
                    "location": request.form.get('location_text', 'SAULT STE. MARIE, MICHIGAN').strip(),
                    "query": request.form.get('query_text', 'Sault+Ste.+Marie,MI,US').strip()
                }
            elif action == 'update_integrations':
                target_state['closings_source'] = request.form.get('closings_source', 'none')
            elif action == 'update_theme':
                target_state['managed_theme'] = request.form.get('managed_theme', '')
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
                target_state.setdefault('slides', []).append(slide)
            elif action == 'add_image_slide':
                file = request.files.get('image')
                if file and file.filename:
                    fname = secure_filename(file.filename)
                    fpath = os.path.join(UPLOAD_FOLDER, f"{int(time.time())}_{fname}")
                    file.save(fpath)
                    slide = {"id": str(int(time.time())), "type": "image", "url": "/" + fpath.replace("\\", "/"), "duration": int(request.form.get('duration', 15)), "start_time": request.form.get('start_time', ''), "end_time": request.form.get('end_time', '')}
                    target_state.setdefault('slides', []).append(slide)
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
                target_state.setdefault('slides', []).append(slide)
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
                    import urllib.parse
                    video_id = None
                    parsed_obj = urllib.parse.urlparse(parsed_url)
                    
                    if 'youtu.be' in parsed_obj.netloc:
                        video_id = parsed_obj.path.lstrip('/')
                    elif '/watch' in parsed_obj.path:
                        qs = urllib.parse.parse_qs(parsed_obj.query)
                        if 'v' in qs: video_id = qs['v'][0]
                    elif '/live/' in parsed_obj.path:
                        video_id = parsed_obj.path.split('/live/')[1]
                        
                    if video_id:
                        parsed_url = f"https://www.youtube.com/embed/{video_id}"
                        
                    if autoplay_req:
                        if 'autoplay=1' not in parsed_url:
                            sep = '&' if '?' in parsed_url else '?'
                            parsed_url += f"{sep}autoplay=1&mute=1"
                    else:
                        parsed_url = parsed_url.replace('autoplay=1', 'autoplay=0').replace('mute=1', '')
                    if 'controls=' not in parsed_url:
                        sep = '&' if '?' in parsed_url else '?'
                        parsed_url += f"{sep}controls=0"

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
                target_state.setdefault('slides', []).append(slide)
            elif action == 'move_slide_up':
                sid = request.form.get('slide_id')
                slides = target_state.get('slides', [])
                idx = next((i for i, s in enumerate(slides) if s.get('id') == sid), -1)
                if idx > 0:
                    slides[idx], slides[idx-1] = slides[idx-1], slides[idx]
                    target_state['slides'] = slides
            elif action == 'move_slide_down':
                sid = request.form.get('slide_id')
                slides = target_state.get('slides', [])
                idx = next((i for i, s in enumerate(slides) if s.get('id') == sid), -1)
                if idx != -1 and idx < len(slides) - 1:
                    slides[idx], slides[idx+1] = slides[idx+1], slides[idx]
                    target_state['slides'] = slides
            elif action == 'toggle_slide':
                sid = request.form.get('slide_id')
                slides = target_state.get('slides', [])
                for s in slides:
                    if s.get('id') == sid:
                        s['hidden'] = not s.get('hidden', False)
                target_state['slides'] = slides
            elif action == 'update_dashboard_slide':
                slides = target_state.get('slides', [])
                for s in slides:
                    if s.get('type') == 'dashboard':
                        s['duration'] = int(request.form.get('duration', 15))
                target_state['slides'] = slides
            elif action == 'undo_slides':
                sh = slide_history.get(slug, {'undo': [], 'redo': []})
                if sh['undo']:
                    sh['redo'].append(copy.deepcopy(target_state.get('slides', [])))
                    target_state['slides'] = sh['undo'].pop()
            elif action == 'redo_slides':
                sh = slide_history.get(slug, {'undo': [], 'redo': []})
                if sh['redo']:
                    sh['undo'].append(copy.deepcopy(target_state.get('slides', [])))
                    target_state['slides'] = sh['redo'].pop()
            elif action == 'delete_slide':
                sid = request.form.get('slide_id')
                for s in target_state.get('slides', []):
                    if s.get('id') == sid and s.get('type') == 'image':
                        try:
                            os.remove(os.path.join(app.root_path, s.get('url').lstrip('/')))
                        except: pass
                target_state['slides'] = [s for s in target_state.get('slides', []) if s.get('id') != sid]
            elif action == 'add_beacon_page':
                new_slug = request.form.get('slug', '').strip().lower().replace(' ', '-')
                title = request.form.get('title', '').strip()
                zipcode = request.form.get('zipcode', '').strip()
                if new_slug and title and zipcode:
                    try:
                        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                            with conn:
                                conn.execute("INSERT INTO beacon_pages (slug, title, zipcode) VALUES (?, ?, ?)", (new_slug, title, zipcode))
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
                loc_slug = request.form.get('location_slug')
                alert_type = request.form.get('alert_type')
                message = request.form.get('alert_message', '').strip()
                color = request.form.get('alert_color', '#d32f2f')
                if 'school_alerts' not in state:
                    state['school_alerts'] = {}
                if alert_type == 'NONE':
                    if loc_slug in state['school_alerts']:
                        del state['school_alerts'][loc_slug]
                else:
                    state['school_alerts'][loc_slug] = {
                        'type': alert_type,
                        'color': color,
                        'message': message,
                        'timestamp': time.time()
                    }
            try:
                with open(STATE_FILE, 'w') as sf: json.dump(state, sf)
            except: pass
        return redirect(request.path)

    with state_lock:
        if slug == "main":
            target_state = state
        else:
            target_state = state.get('tenants', {}).get(slug, {})
            
        slides = target_state.get('slides')
        if not isinstance(slides, list):
            slides = []
            target_state['slides'] = slides
            
        if not any(isinstance(s, dict) and s.get('type') == 'dashboard' for s in slides):
            target_state['slides'].insert(0, {"id": "dashboard", "type": "dashboard", "duration": 15, "hidden": False})
            try:
                with open(STATE_FILE, 'w') as sf: json.dump(state, sf)
            except: pass
            
        try:
            return render_template('admin.html', 
                emergency=target_state.get('emergency') or {}, 
                branding=target_state.get('branding') or {}, 
                main_config=target_state.get('main_config') or {}, 
                slides=target_state.get('slides') or [], 
                managed_theme=target_state.get('managed_theme', ''), 
                beacon_pages=get_beacon_pages(), 
                school_alerts=state.get('school_alerts') or {},
                 closings_source=target_state.get('closings_source', 'none'),
                slug=slug
            )
        except Exception as e:
            if "admin.html" in str(e):
                return "<h1>500 Internal Error: Template Not Found</h1><p>The file <b>admin.html</b> is missing from your <code>templates/</code> directory. Please create and commit it!</p>", 500
            return f"<h1>500 Internal Error</h1><p>An unexpected Python error occurred: <b>{str(e)}</b></p>", 500

@app.route('/api/state')
@app.route('/api/state/<slug>')
def get_state(slug="main"): 
    with state_lock:
        if slug == "main":
            out = state.copy()
        else:
            out = state.copy()
            out.update(state.get('tenants', {}).get(slug, {}))
            
        out["build_timestamp"] = os.environ.get("BUILD_TIMESTAMP", "Local Dev")
        out["agenda_item_count"] = state.get("agenda_item_count", 0)
        if slug != "main":
            out["school_alerts"] = state.get("school_alerts", {})
            out["school_closings"] = state.get("school_closings", {})
            out["emergency"] = state.get("emergency", {})
        return jsonify(out)

@app.route('/api/vote', methods=['POST'])
def submit_vote():
    data = request.json
    if not data: return jsonify(success=False), 400
    item_id = data.get("item_id")
    vote_type = data.get("vote_type")
    action = data.get("action", "add")
    if item_id and vote_type in ['up', 'down']:
        with state_lock:
            if 'agenda_votes' not in state:
                state['agenda_votes'] = {}
            if item_id not in state['agenda_votes']:
                state['agenda_votes'][item_id] = {'up': 0, 'down': 0}
            
            if action == 'add':
                state['agenda_votes'][item_id][vote_type] += 1
            elif action == 'remove':
                state['agenda_votes'][item_id][vote_type] = max(0, state['agenda_votes'][item_id][vote_type] - 1)
                
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
    ua = request.headers.get('User-Agent', '').lower()
    is_car = 'fermata' in ua or 'wv' in ua or request.args.get('auto') == 'true'
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
                           is_car_display=is_car,
                               **state.copy())

@app.route('/api/move/<station>')
def move_buddy(station):
    global manual_override, override_expiry
    manual_override = station
    override_expiry = time.time() + 3600 # Manual override lasts 1 hour
    now = datetime.now(TZ)
    
    # Local fallback responses so web interactions don't trigger expensive API calls
    bubbles = {
        "coffee": "Brewing some camp coffee...", "office": "Mapping the local trails...", 
        "gym": "Chopping digital firewood...", "store": "Grabbing some fresh pasties...", 
        "library": "Reading the old legends...", "garage": "Tuning up the sled...", 
        "park": "Watching the freighters pass...", "kitchen": "Frying up some whitefish...", 
        "bed": "Resting by the fire..."
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
    app.run(host='0.0.0.0', port=5000, debug=False)  # nosec B104
