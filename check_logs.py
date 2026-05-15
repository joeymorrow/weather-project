import sqlite3
import os
import sys
import smtplib
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()

DB_FILE = os.path.join(os.path.dirname(__file__), "data", "system_logs.db")

def send_email(subject, body):
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
        print("\n[+] Email sent successfully to joseph@morrowedge.com")
    except Exception as e:
        print(f"\n[-] Failed to send email: {e}")

if not os.path.exists(DB_FILE):
    print(f"Log database not found at {DB_FILE}")
else:
    output = []
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        output.append("--- LAST 20 SYSTEM LOGS ---")
        c.execute("SELECT timestamp, log_type, message, details FROM logs ORDER BY id DESC LIMIT 20")
        for r in c.fetchall():
            details = r[3][:200] + "..." if r[3] and len(r[3]) > 200 else r[3]
            output.append(f"[{r[0]}] {r[1]}: {r[2]}\nDetails: {details}\n")
            
        output.append("--- RECENT MEMORY METRICS ---")
        c.execute("SELECT timestamp, mem_used_mb FROM metrics ORDER BY timestamp DESC LIMIT 5")
        for r in c.fetchall():
            output.append(f"[{r[0]}] Memory Used: {r[1]:.2f} MB")
            
    full_text = "\n".join(output)
    print(full_text)
    
    if "--email" in sys.argv:
        send_email("BEACON System Logs & Metrics", full_text)