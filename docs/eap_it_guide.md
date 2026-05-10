# 🚨 BEACON EAP Integration & Testing Guide

This document provides exhaustive documentation for IT staff on how to configure, test, and verify the Emergency Awareness Protocol (EAP) capabilities of the Morrow Edge BEACON system.

## 1. System Architecture

BEACON operates as both an **Inbound Receiver** and an **Outbound Broadcaster** for emergency alerts. It adheres visually to the "I Love U Guys" Standard Response Protocol (SRP).

### Inbound Webhook (External Triggers)
BEACON exposes an unauthenticated POST webhook designed to accept JSON payloads from external partners (like Raptor Connect or CAP feeds).
- **Endpoint:** `POST http://<beacon-ip>:5000/api/eap/webhook`
- **Expected Payload:** 
  ```json
  {
    "type": "LOCKDOWN",
    "message": "Locks, Lights, Out of Sight."
  }
  ```
  *(Note: The system also accepts `IncidentType` in place of `type` to natively support Raptor payload structures).*

### Multicast UDP Listener (Local Triggers)
BEACON can bind to local network multicast addresses to listen for alerts from fire panels or Singlewire InformaCast servers.
- Managed via **CoolAdmin** -> **EAP Integration & Dispatch**.
- When an IP (e.g., `239.0.0.1`) and Port (e.g., `5000`) are mapped, BEACON binds a non-blocking socket using `socket.IP_ADD_MEMBERSHIP`.
- Any JSON payload broadcast to this address will instantly trigger the alert across all connected BEACON displays.

### Outbound PWA Dispatcher
The BEACON Dispatch PWA (`/dispatch`) allows staff to trigger alerts globally.
- Requires a 6-digit PIN (Configured in CoolAdmin).
- Bypasses the Webhook and directly pushes UDP Multicast packets to all subscribed endpoints on the local subnet.

---

## 2. Exhaustive Verification & Certification Checklist

IT Staff must perform this checklist to internally certify the BEACON deployment before requesting partner validation.

### Phase 1: Inbound Webhook Verification
- [ ] **Action:** Open a terminal and send a cURL request to the BEACON server:
  ```bash
  curl -X POST http://localhost:5000/api/eap/webhook \
  -H "Content-Type: application/json" \
  -d '{"type": "LOCKDOWN", "message": "TEST: Locks, Lights, Out of Sight."}'
  ```
- [ ] **Expected Result:** All active BEACON dashboards (`/`, `/sault-schools`, etc.) immediately display a RED full-screen takeover with the text "LOCKDOWN" and the provided message.
- [ ] **Action:** Send an ALL CLEAR command:
  ```bash
  curl -X POST http://localhost:5000/api/eap/webhook \
  -H "Content-Type: application/json" \
  -d '{"type": "ALL CLEAR", "message": "Resume normal activities."}'
  ```
- [ ] **Expected Result:** The RED screen changes to GREEN, displaying "ALL CLEAR", and eventually times out or is dismissed, returning to the standard dashboard.

### Phase 2: Outbound PWA & Multicast Verification
- [ ] **Action:** Navigate to `CoolAdmin` and ensure at least one EAP Listener is configured (e.g., `239.0.0.1:5000`).
- [ ] **Action:** Navigate to `http://<beacon-ip>:5000/dispatch` on a mobile device or browser.
- [ ] **Action:** Enter the 6-Digit PIN (Default: `123456`).
- [ ] **Action:** Tap the **HOLD** button and confirm the prompt.
- [ ] **Expected Result:** The PWA should report "Broadcast Successful". 
- [ ] **Expected Result:** The BEACON dashboard should immediately turn PURPLE, displaying "HOLD".

### Phase 3: SRP Adherence & UI Verification
- [ ] Verify **LOCKDOWN** triggers `#d32f2f` (Red).
- [ ] Verify **SECURE** triggers `#ff8c00` (Orange).
- [ ] Verify **HOLD** triggers `#800080` (Purple).
- [ ] Verify **SHELTER** triggers `#1976d2` (Blue).
- [ ] Verify **EVACUATE** triggers `#ff8c00` (Orange/Red).
- [ ] Verify standard informational/weather UI elements are completely obscured during an active alert.

---

## 3. Partner Integration Next Steps

Once the system passes the internal checks above, you can hand the **Pro-Forma** documents to specific vendors:

1. **Raptor Technologies:** Provide `partner_raptor.md` to your Raptor representative to request "Raptor Connect" API keys.
2. **Singlewire InformaCast:** Provide `partner_singlewire.md` to request endpoint compliance testing.