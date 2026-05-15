#!/usr/bin/env python3
import os
import sqlite3
import json
import time
import argparse
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta

try:
    from dotenv import load_dotenv
    import google.genai as genai
    from google.genai import types
    import pytz
    import filelock
except ImportError:
    import subprocess
    import sys
    import os
    print("-> Missing required packages. Installing python-dotenv, google-genai, filelock, and pytz...", flush=True)
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "python-dotenv", "google-genai", "filelock", "pytz"])
    except subprocess.CalledProcessError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "python-dotenv", "google-genai", "filelock", "pytz", "--break-system-packages"])

    print("-> Packages installed! Restarting script to load them...", flush=True)
    os.execv(sys.executable, [sys.executable] + sys.argv)

# Load environment variables from .env
load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

if os.environ.get("HOST_DATA_DIR") and not os.path.exists("/.dockerenv"):
    DATA_DIR = os.environ.get("HOST_DATA_DIR")

DB_FILE = os.path.join(DATA_DIR, "pulse_history.db")
LOG_DB_FILE = os.path.join(DATA_DIR, "system_logs.db")
STATE_FILE = os.path.join(DATA_DIR, "buddy_state.json")

def get_gemini_key():
    try:
        secrets_file = os.path.join(DATA_DIR, "secrets.json")
        if os.path.exists(secrets_file):
            with open(secrets_file, 'r') as f:
                secrets = json.load(f)
                if secrets.get("GEMINI_API_KEY"):
                    return secrets.get("GEMINI_API_KEY")
    except: pass
    return os.environ.get("GEMINI_API_KEY", "")

G_KEY = get_gemini_key()

client = genai.Client(api_key=G_KEY)

_best_models_cache = []

def is_gemini_disabled():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                return json.load(f).get("gemini_api_disabled", False)
    except: pass
    return False

def set_gemini_disabled():
    try:
        st = {}
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                st = json.load(f)
        if st.get("gemini_api_disabled"): return False
        st["gemini_api_disabled"] = True
        with open(STATE_FILE, 'w') as f: json.dump(st, f)
        return True
    except: return False

def check_and_log_api_usage(api_name, caller_context):
    try:
        g_daily = 1400
        g_hourly = 100
        reset_at_midnight = False
        context_limit = 30
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                st = json.load(f)
                limits = st.get("api_limits", {})
                g_daily = limits.get("gemini_daily", 1400)
                g_hourly = limits.get("gemini_hourly", 100)
                context_limit = limits.get("per_endpoint_hourly", 30)
                reset_at_midnight = limits.get("reset_at_midnight", False)
                if limits.get("auto_free_tier", True):
                    g_daily = min(g_daily, 1400)

        with sqlite3.connect(LOG_DB_FILE, timeout=10) as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM api_usage_log WHERE api_name=? AND timestamp >= datetime('now', '-1 hour')", (api_name,))
            hourly = c.fetchone()[0]
            
            c.execute("SELECT COUNT(*) FROM api_usage_log WHERE api_name=? AND caller_context=? AND timestamp >= datetime('now', '-1 hour')", (api_name, caller_context))
            context_hourly_count = c.fetchone()[0]
            
            if context_hourly_count >= context_limit and caller_context != 'model_discovery':
                print(f"[GOVERNOR] Auto-throttling {caller_context} on {api_name} ({context_hourly_count}/{context_limit} calls/hr)", flush=True)
                return False

            if reset_at_midnight:
                c.execute("SELECT COUNT(*) FROM api_usage_log WHERE api_name=? AND timestamp >= date('now')", (api_name,))
            else:
                c.execute("SELECT COUNT(*) FROM api_usage_log WHERE api_name=? AND timestamp >= datetime('now', '-24 hours')", (api_name,))
            daily = c.fetchone()[0]
            
            if daily >= g_daily or hourly >= g_hourly:
                return False
                
            conn.execute("INSERT INTO api_usage_log (api_name, caller_context, status) VALUES (?, ?, 'in_progress')", (api_name, caller_context))
            conn.commit()
            return True
    except: return True

def get_best_models():
    global _best_models_cache
    if _best_models_cache:
        return _best_models_cache
    try:
        if not check_and_log_api_usage('gemini', 'model_discovery'):
            return ["gemini-2.5-flash", "gemini-1.5-flash"]
        all_m = list(client.models.list())
        ranked = []
        import re
        for m in all_m:
            n = m.name.lower()
            # Strip models/ prefix to prevent 404s with the v1beta SDK
            n_clean = m.name.replace("models/", "")
            
            # Block multimodal/experimental models from draining quota on text tasks
            if any(x in n for x in ["tts", "image", "audio", "vision", "embedding", "pro", "ultra", "learnmath", "veo", "preview", "live", "experimental"]):
                continue
                
            if hasattr(m, 'supported_generation_methods') and m.supported_generation_methods:
                if 'generateContent' not in m.supported_generation_methods:
                    continue

            score = 0
            if "lite" in n: score += 100
            if "flash" in n: score += 200
            if "preview" in n: score -= 500
            
            match = re.search(r'(\d+\.\d+)', n)
            if match:
                score += float(match.group(1)) * 1000
                
            if score > 0: ranked.append((n_clean, score))
        ranked.sort(key=lambda x: x[1], reverse=True)
        _best_models_cache = [r[0] for r in ranked] if ranked else ["gemini-2.5-flash", "gemini-1.5-flash"]
    except Exception:
        _best_models_cache = ["gemini-2.5-flash", "gemini-1.5-flash"]
    return _best_models_cache

def send_report_email(subject, body):
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = os.environ.get("SMTP_USER") or "buddy-alerts@morrowedge.com"
    msg['To'] = "joseph@morrowedge.com"

    smtp_server = os.environ.get("SMTP_SERVER") or "localhost"
    smtp_port = int(os.environ.get("SMTP_PORT") or 587)
    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            if os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASS"):
                server.starttls()
                server.login(os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASS"))
            server.send_message(msg)
        print("-> Email report sent successfully.")
    except Exception as e:
        print(f"-> [ERROR] Failed to send email: {e}")

def setup_db():
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS pulses (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            date TEXT,
                            text TEXT UNIQUE
                         )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS hallucinations_log (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            retrieved_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                            date TEXT,
                            text TEXT,
                            reason TEXT
                         )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS old_pulses (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            archived_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                            date TEXT,
                            text TEXT
                         )''')
        try:
            conn.execute("ALTER TABLE old_pulses ADD COLUMN location TEXT DEFAULT ''")
            conn.execute("ALTER TABLE old_pulses ADD COLUMN details TEXT DEFAULT '{}'")
        except sqlite3.OperationalError:
            pass
        conn.execute('''CREATE TABLE IF NOT EXISTS garage_sales (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            date TEXT,
                            text TEXT UNIQUE,
                            location TEXT
                         )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS sault_tribe (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            date TEXT,
                            text TEXT UNIQUE,
                            location TEXT
                         )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS sault_schools (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            date TEXT,
                            text TEXT UNIQUE,
                            location TEXT
                         )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS hallucination_checked_texts (
                            text TEXT PRIMARY KEY
                         )''')

def check_hallucination(text, date_str, item_type="pulse"):
    if item_type == "garage_sale":
        rules = "- Buddy acts as a local scout for garage sales. He searches for VERIFIABLE garage, yard, or estate sales happening today or tomorrow in Sault Ste. Marie, Michigan or nearby towns within a 45-minute drive (e.g., Brimley, Kinross, Pickford). If the text mentions specific addresses, times, or sales that did not occur or are not scheduled, IT IS A HALLUCINATION. He must EXCLUDE Canadian garage sales (in Sault Ste. Marie, Ontario) and far-away cities."
        instructions = "1. Search Google to see if this specific garage/yard sale was advertised for this location and date in Michigan's Eastern Upper Peninsula.\n2. If you cannot find any proof of a sale at this address/time, it is a hallucination.\n3. If the sale is located in Canada (Ontario) or a far away city (like Chicago or Detroit), flag it as a hallucination.\n4. If the location/address provided is just a number without a street or road name, it is invalid and MUST be flagged as a hallucination."
    elif item_type == "sault_tribe":
        rules = "- Buddy acts as a local scout for Sault Ste. Marie Tribe of Chippewa Indians news and events. He searches for VERIFIABLE tribal news, cultural events, or announcements. Fake events or workshops are HALLUCINATIONS."
        instructions = "1. Search Google to see if this specific Sault Tribe event/news was published or advertised for this location and date.\n2. If you cannot find any proof, it is a hallucination."
    elif item_type == "sault_schools":
        rules = "- Buddy acts as a scout for Sault Area Public Schools events (Malcolm High, Sault High, Sault Middle, Sault Elementary, athletics). He searches for VERIFIABLE school news, board meetings, and sports schedules. Fake events are HALLUCINATIONS."
        instructions = "1. Search Google/School Calendars to see if this specific Sault Schools event was published for this location and date.\n2. If you cannot find any proof, it is a hallucination."
    else:
        rules = """- During the daytime (6 AM - 9:30 PM), Buddy acts as a local speechwriter. He searches for VERIFIABLE recent local news, events, or acts of kindness.
- During late night (9:30 PM - 6 AM), Buddy acts as a poetic night-owl.
- DO NOT DELETE generic, harmless community gatherings or poetic observations. ONLY flag as a hallucination if it cites a highly specific, fake news event (e.g. a fake bombing, a named festival that doesn't exist). Err heavily on the side of VALID (hallucinated: false)."""
        instructions = "1. Determine if the text sounds like a poetic nighttime observation or generic community gathering. If it does, it is NOT a hallucination (valid).\n2. ONLY flag as a hallucination if it cites a highly specific, fabricated news event. Err heavily on the side of VALID (hallucinated: false)."

    prompt = f"""
You are a strict AI Hallucination Evaluator for Beacon Buddy, an ambient dashboard in Sault Ste. Marie, Michigan.
Evaluate if the following generated text is a hallucination or an intended response.

- **Date Recorded:** {date_str}
- **Text to evaluate:** "{text}"
- **Item Type:** {item_type}

**BEACON BUDDY GENERATION RULES (Context):**
{rules}

**Instructions:**
{instructions}
3. Return ONLY a valid JSON object in this exact format:

{{
  "hallucinated": true/false,
  "reason": "Brief explanation of why you made this determination. Mention what you searched for and what you found (or didn't find)."
}}
"""
    models_to_try = get_best_models()
    last_error = None

    for m_id in models_to_try:
        try:
            # 1. RPM THROTTLE
            import filelock
            throttle_lock = filelock.FileLock(os.path.join(DATA_DIR, "gemini_rpm.lock"))
            with throttle_lock:
                while True:
                    try:
                        with sqlite3.connect(LOG_DB_FILE, timeout=10) as conn:
                            c = conn.cursor()
                            c.execute("SELECT COUNT(*) FROM api_usage_log WHERE api_name='gemini' AND timestamp >= datetime('now', '-1 minute')")
                            if c.fetchone()[0] >= 14:
                                print("[THROTTLE] Approaching Gemini 15 RPM limit. Sleeping 5s...", flush=True)
                                time.sleep(5)
                            else:
                                break
                    except Exception: 
                        break

            if not check_and_log_api_usage('gemini', 'cleanup_hallucinations'):
                if set_gemini_disabled():
                    send_report_email(
                        "[CRITICAL - BEACON BUDDY] API Circuit Breaker Tripped",
                        "Gemini API exceeded limits during hallucination cleanup. AI generation disabled."
                    )
                return False, "Gemini API Circuit Breaker Limit Reached"
                
            response = client.models.generate_content(
                model=m_id,
                contents=prompt,
                config=types.GenerateContentConfig(tools=[{"google_search": {}}])
            )
            
            tokens = 0
            try:
                if hasattr(response, 'usage_metadata') and response.usage_metadata and response.usage_metadata.total_token_count:
                    tokens = response.usage_metadata.total_token_count
            except Exception: pass

            try:
                with sqlite3.connect(LOG_DB_FILE, timeout=10) as conn:
                    conn.execute("UPDATE api_usage_log SET status='completed', ended_at=datetime('now'), tokens_used=? WHERE id = (SELECT MAX(id) FROM api_usage_log WHERE api_name='gemini' AND caller_context='cleanup_hallucinations' AND status='in_progress')", (tokens,))
                    conn.commit()
            except Exception: pass

            resp_text = response.text or ""
            json_start = resp_text.find('{')
            json_end = resp_text.rfind('}')
            if json_start == -1 or json_end == -1:
                raise ValueError("No valid JSON found in model response.")
            json_str = resp_text[json_start:json_end+1]
            data = json.loads(json_str)
            return data.get("hallucinated", False), data.get("reason", "No reason provided")
        except Exception as e:
            try:
                with sqlite3.connect(LOG_DB_FILE, timeout=10) as conn:
                    conn.execute("UPDATE api_usage_log SET status='failed', ended_at=datetime('now'), details=? WHERE id = (SELECT MAX(id) FROM api_usage_log WHERE api_name='gemini' AND caller_context='cleanup_hallucinations' AND status='in_progress')", (str(e)[:100],))
                    conn.commit()
            except Exception: pass
            
            err_str = str(e).lower()
            if "429" in err_str or "exhausted" in err_str or "quota" in err_str:
                print(f"[WARNING] 429 Rate Limit hit during cleanup. Skipping item for now. {e}", flush=True)
                time.sleep(15)
                return None, "Gemini API 429 Rate Limit Hit"
            if "404" in err_str or "not found" in err_str:
                last_error = e
                continue
            if "hallucination" in err_str or "safety" in err_str or "recitation" in err_str or "blocked" in err_str:
                return None, "Blocked by Safety/Hallucination Filter"
            last_error = e
            continue

    print(f"Error checking text: {last_error}")
    return None, str(last_error)

RED = '\033[91m'
RESET = '\033[0m'

def main():
    parser = argparse.ArgumentParser(description="Hallucination Cleanup Script")
    parser.add_argument("--run-auto", action="store_true", help="Run automated cleanup round and email results.")
    parser.add_argument("--list-previous-hallucinations", action="store_true", help="List dates/names and retrieval times.")
    parser.add_argument("--print-previous-hallucinations", action="store_true", help="Print full details of all stored hallucinations.")
    parser.add_argument("--list-old-pulses", action="store_true", help="List archived old pulses.")
    parser.add_argument("--db", type=str, help="Override the path to the database file.")
    args = parser.parse_args()

    global DB_FILE
    if args.db:
        DB_FILE = os.path.abspath(args.db)

    setup_db()

    if is_gemini_disabled():
        print("Gemini API is currently disabled due to quota exhaustion. Skipping cleanup.", flush=True)
        return

    if args.list_previous_hallucinations:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, date, retrieved_at FROM hallucinations_log ORDER BY retrieved_at DESC")
            rows = cursor.fetchall()
            print("--- Previous Hallucinations (List) ---")
            if not rows: print("No recorded hallucinations.")
            for r in rows:
                print(f"ID: {r[0]} | Date: {r[1]} | Retrieved At: {r[2]}")
        return

    if args.print_previous_hallucinations:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, date, retrieved_at, text, reason FROM hallucinations_log ORDER BY retrieved_at DESC")
            rows = cursor.fetchall()
            print("--- Previous Hallucinations (Full Details) ---")
            if not rows: print("No recorded hallucinations.")
            for r in rows:
                print(f"[{r[0]}] Date: {r[1]} | Retrieved: {r[2]}\nText: {r[3]}\nReason: {r[4]}\n")
        return

    if args.list_old_pulses:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT id, date, archived_at, text, location, details FROM old_pulses ORDER BY archived_at DESC")
                has_details = True
            except sqlite3.OperationalError:
                cursor.execute("SELECT id, date, archived_at, text FROM old_pulses ORDER BY archived_at DESC")
                has_details = False
            rows = cursor.fetchall()
            print("--- Archived Old Pulses ---")
            if not rows: print("No old pulses archived.")
            for r in rows:
                if has_details:
                    print(f"[{r[0]}] Date: {r[1]} | Archived At: {r[2]} | Loc: {r[4]}\nText: {r[3]}\nDetails: {r[5]}\n")
                else:
                    print(f"[{r[0]}] Date: {r[1]} | Archived At: {r[2]}\nText: {r[3]}\n")
        return

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT id, date, text, location, details FROM pulses")
        except sqlite3.OperationalError:
            cursor.execute("SELECT id, date, text, '' as location, '{}' as details FROM pulses")
        pulses = cursor.fetchall()

        cursor.execute("SELECT id, date, text FROM garage_sales")
        garage_sales = cursor.fetchall()

        cursor.execute("SELECT id, date, text FROM sault_tribe")
        sault_tribe = cursor.fetchall()
        
        cursor.execute("SELECT id, date, text FROM sault_schools")
        sault_schools = cursor.fetchall()

        cursor.execute("SELECT text FROM hallucination_checked_texts")
        checked_texts = set(r[0] for r in cursor.fetchall())

        items_to_check = []
        now = datetime.now(pytz.timezone('America/Detroit'))
        archived_count = 0

        # Age out Pulses (> 72 hours)
        for pulse_id, date_str, text, location, details in pulses:
            try:
                date_clean = date_str.split('[')[0].strip()
                try: dt_naive = datetime.strptime(f"{now.year} {date_clean}", "%Y %B %d, %I:%M %p")
                except ValueError: dt_naive = datetime.strptime(f"{now.year} {date_clean}", "%Y %B %d")
                dt = pytz.timezone('America/Detroit').localize(dt_naive)
                if dt > now: dt = dt.replace(year=now.year - 1)

                age = now - dt
                if age > timedelta(hours=72):
                    cursor.execute("INSERT INTO old_pulses (date, text, location, details) VALUES (?, ?, ?, ?)", (date_str, text, location, details))
                    cursor.execute("DELETE FROM pulses WHERE id = ?", (pulse_id,))
                    conn.commit()
                    print(f"-> [ARCHIVED] Pulse ID {pulse_id} ({date_str}) is >72 hours old.", flush=True)
                    archived_count += 1
                    continue
            except ValueError: pass # Skip unparsable or malformed legacy dates

            if text not in checked_texts:
                items_to_check.append(("pulse", pulse_id, date_str, text))

        # Age out Garage Sales (> 48 hours to preserve today and tomorrow only)
        for sale_id, date_str, text in garage_sales:
            try:
                date_clean = date_str.split('[')[0].strip()
                try: dt_naive = datetime.strptime(f"{now.year} {date_clean}", "%Y %B %d, %I:%M %p")
                except ValueError: dt_naive = datetime.strptime(f"{now.year} {date_clean}", "%Y %B %d")
                dt = pytz.timezone('America/Detroit').localize(dt_naive)
                if dt > now: dt = dt.replace(year=now.year - 1)

                age = now - dt
                if age > timedelta(hours=72):
                    cursor.execute("DELETE FROM garage_sales WHERE id = ?", (sale_id,))
                    conn.commit()
                    archived_count += 1
                    continue
            except ValueError: pass

            if text not in checked_texts:
                items_to_check.append(("garage_sale", sale_id, date_str, text))

        # Age out Sault Tribe (> 72 hours)
        for tribe_id, date_str, text in sault_tribe:
            try:
                date_clean = date_str.split('[')[0].strip()
                try: dt_naive = datetime.strptime(f"{now.year} {date_clean}", "%Y %B %d, %I:%M %p")
                except ValueError: dt_naive = datetime.strptime(f"{now.year} {date_clean}", "%Y %B %d")
                dt = pytz.timezone('America/Detroit').localize(dt_naive)
                if dt > now: dt = dt.replace(year=now.year - 1)

                age = now - dt
                if age > timedelta(hours=72):
                    cursor.execute("DELETE FROM sault_tribe WHERE id = ?", (tribe_id,))
                    conn.commit()
                    archived_count += 1
                    continue
            except ValueError: pass

            if text not in checked_texts:
                items_to_check.append(("sault_tribe", tribe_id, date_str, text))

        # Age out Sault Schools (> 72 hours)
        for school_id, date_str, text in sault_schools:
            try:
                date_clean = date_str.split('[')[0].strip()
                try: dt_naive = datetime.strptime(f"{now.year} {date_clean}", "%Y %B %d, %I:%M %p")
                except ValueError: dt_naive = datetime.strptime(f"{now.year} {date_clean}", "%Y %B %d")
                dt = pytz.timezone('America/Detroit').localize(dt_naive)
                if dt > now: dt = dt.replace(year=now.year - 1)

                age = now - dt
                if age > timedelta(hours=72):
                    cursor.execute("DELETE FROM sault_schools WHERE id = ?", (school_id,))
                    conn.commit()
                    archived_count += 1
                    continue
            except ValueError: pass
            if text not in checked_texts:
                items_to_check.append(("sault_schools", school_id, date_str, text))

        total_items = len(items_to_check)
        
        # Indiscriminate API Prevention: Cap the maximum checks per loop
        if total_items > 40:
            print(f"Limiting to 40 items per run to prevent API quota drain (Total pending: {total_items}).\n")
            items_to_check = items_to_check[:40]
            total_items = 40

        print(f"Found {total_items} items to check for hallucinations (Archived/Deleted {archived_count} old items).\n")

        round_results = []

        for idx, (item_type, item_id, date, text) in enumerate(items_to_check):
            print(f"Analyzing [{idx+1} of {total_items}] [{item_type.upper()} {item_id}] {date}: {text}", flush=True)
            is_hallucinated, reason = check_hallucination(text, date, item_type)

            if is_hallucinated:
                print(f"{RED}-> [HALLUCINATION DETECTED] {reason}{RESET}", flush=True)
                cursor.execute("INSERT INTO hallucinations_log (date, text, reason) VALUES (?, ?, ?)", (date, text, reason))
                if item_type == "pulse":
                    cursor.execute("DELETE FROM pulses WHERE id = ?", (item_id,))
                elif item_type == "garage_sale":
                    cursor.execute("DELETE FROM garage_sales WHERE id = ?", (item_id,))
                elif item_type == "sault_schools":
                    cursor.execute("DELETE FROM sault_schools WHERE id = ?", (item_id,))
                else:
                    cursor.execute("DELETE FROM sault_tribe WHERE id = ?", (item_id,))
                conn.commit()
                print(f"{RED}-> Deleted {item_type} ID: {item_id}\n{RESET}", flush=True)
                round_results.append((date, text, reason))
            elif is_hallucinated is False:
                print(f"-> [VALID] {reason}\n", flush=True)
                cursor.execute("INSERT OR IGNORE INTO hallucination_checked_texts (text) VALUES (?)", (text,))
                conn.commit()
            else:
                print(f"-> [ERROR/SKIPPED] {reason}. Will retry next round.\n", flush=True)

            time.sleep(1) # Prevent hitting API rate limits

        # Keep only the last 100 entries in the log
        cursor.execute('''DELETE FROM hallucinations_log
                          WHERE id NOT IN (
                              SELECT id FROM hallucinations_log
                              ORDER BY retrieved_at DESC
                              LIMIT 100
                          )''')

        cursor.execute('''DELETE FROM hallucination_checked_texts 
                          WHERE text NOT IN (SELECT text FROM pulses)
                          AND text NOT IN (SELECT text FROM garage_sales)
                          AND text NOT IN (SELECT text FROM sault_tribe)
                          AND text NOT IN (SELECT text FROM sault_schools)''')
        conn.commit()

        print("Cleanup complete!", flush=True)

        if args.run_auto:
            if not round_results:
                subject = "[BEACON BUDDY] - Hallucination Cleanup: None Found"
                body = "The scheduled hallucination cleanup ran successfully.\n\nNo hallucinations were detected in this round."
            else:
                subject = f"[BEACON BUDDY] - Hallucination Cleanup: {len(round_results)} Removed"
                body = f"The scheduled hallucination cleanup detected and removed {len(round_results)} hallucinated entries:\n\n"
                for res in round_results:
                    body += f"Date: {res[0]}\nText: {res[1]}\nReason: {res[2]}\n\n"

            send_report_email(subject, body)
        else:
            if round_results:
                print(f"\n{RED}--- Summary: {len(round_results)} Hallucinations Removed ---{RESET}")
                for res in round_results:
                    print(f"{RED}Date: {res[0]}\nText: {res[1]}\nReason: {res[2]}\n{RESET}")
            else:
                print("\n--- Summary ---")
                print("No hallucinations were detected during this run.")

if __name__ == "__main__":
    main()