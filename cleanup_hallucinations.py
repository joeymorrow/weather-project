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
except ImportError:
    import subprocess
    import sys
    import os
    print("-> Missing required packages. Installing python-dotenv, google-genai, and pytz...", flush=True)
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "python-dotenv", "google-genai", "pytz"])
    except subprocess.CalledProcessError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "python-dotenv", "google-genai", "pytz", "--break-system-packages"])
    
    print("-> Packages installed! Restarting script to load them...", flush=True)
    os.execv(sys.executable, [sys.executable] + sys.argv)

# Load environment variables from .env
load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

if os.environ.get("HOST_DATA_DIR") and not os.path.exists("/.dockerenv"):
    DATA_DIR = os.environ.get("HOST_DATA_DIR")

DB_FILE = os.path.join(DATA_DIR, "pulse_history.db")
G_KEY = os.environ.get("GEMINI_API_KEY", "")

if not G_KEY:
    print("Error: GEMINI_API_KEY not found in your environment/ .env file.")
    exit(1)

client = genai.Client(api_key=G_KEY)

_best_models_cache = []
def get_best_models():
    global _best_models_cache
    if _best_models_cache:
        return _best_models_cache
    try:
        all_m = list(client.models.list())
        ranked = []
        for m in all_m:
            n = m.name.lower()
            # Strip models/ prefix to prevent 404s with the v1beta SDK
            n_clean = m.name.replace("models/", "")
            score = 0
            if "3.1-flash-lite" in n: score = 2000
            elif "2.5-flash-lite" in n: score = 1500
            elif "3-flash" in n: score = 1000
            elif "2.5-flash" in n: score = 800
            elif "1.5-flash" in n: score = 500
            if "pro" in n or "ultra" in n: score -= 5000
            if score > 0: ranked.append((n_clean, score))
        ranked.sort(key=lambda x: x[1], reverse=True)
        _best_models_cache = [r[0] for r in ranked] if ranked else ["gemini-2.5-flash", "gemini-1.5-flash"]
    except Exception:
        _best_models_cache = ["gemini-2.5-flash", "gemini-1.5-flash"]
    return _best_models_cache

def send_report_email(subject, body):
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = os.environ.get("SMTP_USER", "buddy-alerts@morrowedge.com")
    msg['To'] = "joseph@morrowedge.com"
    
    smtp_server = os.environ.get("SMTP_SERVER", "localhost")
    smtp_port = int(os.environ.get("SMTP_PORT", 587))
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

def check_hallucination(text, date_str, item_type="pulse"):
    if item_type == "garage_sale":
        rules = "- Buddy acts as a local scout for garage sales. He searches for VERIFIABLE garage, yard, or estate sales happening today or tomorrow. If the text mentions specific addresses, times, or sales that did not occur or are not scheduled, IT IS A HALLUCINATION."
        instructions = "1. Search Google to see if this specific garage/yard sale was advertised for this location and date.\n2. If you cannot find any proof of a sale at this address/time, it is a hallucination."
    elif item_type == "sault_tribe":
        rules = "- Buddy acts as a local scout for Sault Ste. Marie Tribe of Chippewa Indians news and events. He searches for VERIFIABLE tribal news, cultural events, or announcements. Fake events or workshops are HALLUCINATIONS."
        instructions = "1. Search Google to see if this specific Sault Tribe event/news was published or advertised for this location and date.\n2. If you cannot find any proof, it is a hallucination."
    else:
        rules = """- During the daytime (6 AM - 9:30 PM), Buddy acts as a local speechwriter. He searches for VERIFIABLE recent local news, events, or acts of kindness. If the text mentions specific names, workshops, or times that did not occur in real life, IT IS A HALLUCINATION.
- During late night (9:30 PM - 6 AM), Buddy acts as a poetic night-owl. He provides quiet, atmospheric observations about the city's nocturnal rhythm. These poetic, non-specific observations are INTENDED and are NOT hallucinations."""
        instructions = "1. Determine if the text sounds like a poetic nighttime observation. If it does, it is NOT a hallucination (valid).\n2. If it sounds like specific daytime news, use Google Search to verify it. Fake events with specific unverified details are hallucinations."

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
            response = client.models.generate_content(
                model=m_id,
                contents=prompt,
                config=types.GenerateContentConfig(tools=[{"google_search": {}}])
            )
            resp_text = response.text
            json_start = resp_text.find('{')
            json_end = resp_text.rfind('}')
            if json_start == -1 or json_end == -1:
                raise ValueError("No valid JSON found in model response.")
            json_str = resp_text[json_start:json_end+1]
            data = json.loads(json_str)
            return data.get("hallucinated", False), data.get("reason", "No reason provided")
        except Exception as e:
            last_error = e
            continue
            
    print(f"Error checking text: {last_error}")
    return False, str(last_error)

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
            cursor.execute("SELECT id, date, archived_at, text FROM old_pulses ORDER BY archived_at DESC")
            rows = cursor.fetchall()
            print("--- Archived Old Pulses ---")
            if not rows: print("No old pulses archived.")
            for r in rows:
                print(f"[{r[0]}] Date: {r[1]} | Archived At: {r[2]}\nText: {r[3]}\n")
        return

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, date, text FROM pulses")
        pulses = cursor.fetchall()
        
        cursor.execute("SELECT id, date, text FROM garage_sales")
        garage_sales = cursor.fetchall()
        
        cursor.execute("SELECT id, date, text FROM sault_tribe")
        sault_tribe = cursor.fetchall()

        items_to_check = []
        now = datetime.now(pytz.timezone('America/Detroit'))
        archived_count = 0

        # Age out Pulses (> 36 hours)
        for pulse_id, date_str, text in pulses:
            try:
                # Reconstruct datetime from pulse text ('May 09') assuming current year
                dt_naive = datetime.strptime(f"{now.year} {date_str}", "%Y %B %d")
                dt = pytz.timezone('America/Detroit').localize(dt_naive)
                if dt > now: dt = dt.replace(year=now.year - 1)
                
                age = now - dt
                if age > timedelta(hours=36):
                    cursor.execute("INSERT INTO old_pulses (date, text) VALUES (?, ?)", (date_str, text))
                    cursor.execute("DELETE FROM pulses WHERE id = ?", (pulse_id,))
                    conn.commit()
                    print(f"-> [ARCHIVED] Pulse ID {pulse_id} ({date_str}) is >36 hours old.", flush=True)
                    archived_count += 1
                    continue
            except ValueError: pass # Skip unparsable or malformed legacy dates
                
            items_to_check.append(("pulse", pulse_id, date_str, text))
            
        # Age out Garage Sales (> 48 hours to preserve today and tomorrow only)
        for sale_id, date_str, text in garage_sales:
            try:
                dt_naive = datetime.strptime(f"{now.year} {date_str}", "%Y %B %d")
                dt = pytz.timezone('America/Detroit').localize(dt_naive)
                if dt > now: dt = dt.replace(year=now.year - 1)
                
                age = now - dt
                if age > timedelta(hours=48):
                    cursor.execute("DELETE FROM garage_sales WHERE id = ?", (sale_id,))
                    conn.commit()
                    archived_count += 1
                    continue
            except ValueError: pass
                
            items_to_check.append(("garage_sale", sale_id, date_str, text))
            
        # Age out Sault Tribe (> 72 hours)
        for tribe_id, date_str, text in sault_tribe:
            try:
                dt_naive = datetime.strptime(f"{now.year} {date_str}", "%Y %B %d")
                dt = pytz.timezone('America/Detroit').localize(dt_naive)
                if dt > now: dt = dt.replace(year=now.year - 1)
                
                age = now - dt
                if age > timedelta(hours=72):
                    cursor.execute("DELETE FROM sault_tribe WHERE id = ?", (tribe_id,))
                    conn.commit()
                    archived_count += 1
                    continue
            except ValueError: pass
                
            items_to_check.append(("sault_tribe", tribe_id, date_str, text))

        print(f"Found {len(items_to_check)} items to check for hallucinations (Archived/Deleted {archived_count} old items).\n")
        
        round_results = []

        for item_type, item_id, date, text in items_to_check:
            print(f"Analyzing [{item_type.upper()} {item_id}] {date}: {text}", flush=True)
            is_hallucinated, reason = check_hallucination(text, date, item_type)
            
            if is_hallucinated:
                print(f"{RED}-> [HALLUCINATION DETECTED] {reason}{RESET}", flush=True)
                cursor.execute("INSERT INTO hallucinations_log (date, text, reason) VALUES (?, ?, ?)", (date, text, reason))
                if item_type == "pulse":
                    cursor.execute("DELETE FROM pulses WHERE id = ?", (item_id,))
                elif item_type == "garage_sale":
                    cursor.execute("DELETE FROM garage_sales WHERE id = ?", (item_id,))
                else:
                    cursor.execute("DELETE FROM sault_tribe WHERE id = ?", (item_id,))
                conn.commit()
                print(f"{RED}-> Deleted {item_type} ID: {item_id}\n{RESET}", flush=True)
                round_results.append((date, text, reason))
            else:
                print(f"-> [VALID] {reason}\n", flush=True)
                
            time.sleep(1) # Prevent hitting API rate limits
            
        # Keep only the last 100 entries in the log
        cursor.execute('''DELETE FROM hallucinations_log 
                          WHERE id NOT IN (
                              SELECT id FROM hallucinations_log 
                              ORDER BY retrieved_at DESC 
                              LIMIT 100
                          )''')
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