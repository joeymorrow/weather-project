import socket
import threading
import sqlite3
from contextlib import closing

def log_drop(reason, details):
    print(f"[SHIELD DROP] {reason}: {details[:100]}...", flush=True)
    try:
        with closing(sqlite3.connect('data/system_logs.db', timeout=5)) as conn:
            with conn:
                conn.execute("INSERT INTO logs (log_type, message, details) VALUES (?, ?, ?)",
                             ("PROXY_SHIELD", f"Novelty Shield Dropped Request: {reason}", details))
    except Exception as e:
        pass

def forward(source, destination):
    try:
        while True:
            data = source.recv(4096)
            if len(data) == 0:
                break
            destination.sendall(data)
    except:
        pass
    finally:
        source.close()
        destination.close()

def handle_client(client_socket):
    try:
        request = b""
        # TCP is a streaming protocol. We must buffer until the full HTTP header arrives!
        while b"\r\n\r\n" not in request and len(request) < 8192:
            chunk = client_socket.recv(4096)
            if not chunk:
                break
            request += chunk
            
        if not request:
            client_socket.close()
            return
            
        req_str = request.decode('utf-8', errors='ignore')
        lines = req_str.split("\r\n")
        first_line = lines[0] if lines else ""
        
        # === THE NOVELTY CHECKS ===
        
        # 1. Null-byte poisoning check (Classic zero-day vector for C-based parsers)
        if b'\x00' in request:
            log_drop("Null-byte poisoning", req_str)
            client_socket.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\nNovelty proxy: No null bytes.")
            client_socket.close()
            return

        # 2. The "Ancient Script" Check (Blocks HTTP/1.0 which bots love)
        if "HTTP/1.0" in first_line:
            log_drop("Ancient Script (HTTP/1.0)", first_line)
            client_socket.sendall(b"HTTP/1.1 426 Upgrade Required\r\n\r\nNovelty proxy: Upgrade to HTTP/1.1.")
            client_socket.close()
            return
            
        # 3. The "Too Fast" Bot Check (Real browsers & Cloudflare send 10+ headers; basic scripts send 2-4)
        if len(lines) < 6:
            log_drop("Missing Headers", req_str)
            client_socket.sendall(b"HTTP/1.1 418 I'm a teapot\r\n\r\nNovelty proxy: Where are your headers?")
            client_socket.close()
            return
            
        # 4. The "Nosy Scanner" Check (Drop connections looking for typical vuln files)
        if any(bad in first_line.lower() for bad in ['.php', '.env', 'wp-admin', '.git', 'eval(', 'base64']):
            log_drop("Nosy Scanner", first_line)
            client_socket.sendall(b"HTTP/1.1 444 No Response\r\n\r\n")
            client_socket.close()
            return

        # Forward the clean, validated traffic to the internal Gunicorn container
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.connect(('weather-dashboard', 5000))
        server_socket.sendall(request)
        
        # Start bidirectional piping
        threading.Thread(target=forward, args=(client_socket, server_socket), daemon=True).start()
        threading.Thread(target=forward, args=(server_socket, client_socket), daemon=True).start()

    except Exception as e:
        client_socket.close()

def start_proxy():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('0.0.0.0', 8080))
    server.listen(100)
    print("[SLIM PROXY] Novelty shield active on port 8080. Forwarding to weather-dashboard:5000...", flush=True)
    
    while True:
        client_sock, addr = server.accept()
        threading.Thread(target=handle_client, args=(client_sock,), daemon=True).start()

if __name__ == '__main__':
    start_proxy()