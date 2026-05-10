# 📢 BEACON Integration Request: Singlewire InformaCast

**To:** Singlewire Partner Integration Team  
**From:** Morrow Edge | BEACON IT Administration  
**Subject:** Request for InformaCast-Compliant Endpoint Certification  

## Overview
The **Morrow Edge | BEACON Dashboard** is an enterprise digital signage platform. We are formally requesting technical review and certification to operate as an **InformaCast-Compliant Digital Signage Endpoint**.

Our system is architected to seamlessly integrate with local network safety protocols, ensuring that critical alerts bypass routine visual content (weather, news, scheduling) immediately upon receipt.

## Technical Architecture

BEACON supports two primary integration pathways for Singlewire InformaCast environments:

### 1. Multicast UDP Listener (Primary)
BEACON instances actively bind to local subnet Multicast IP/Port streams using `socket.IP_ADD_MEMBERSHIP`. 
- **Format:** JSON Payloads transmitted over UDP.
- **Advantage:** Allows instant, simultaneous triggering of all displays on a campus without relying on external internet routing or bottlenecking the InformaCast server.

### 2. CAP 1.2 / Webhook Ingestion (Fallback)
BEACON exposes an inbound webhook mapping for cloud-to-cloud or direct server-to-server alerts.
- **Endpoint URL:** `https://<beacon-host>/api/eap/webhook`
- **Method:** `POST`

## Verification & Certification Checklist

To certify BEACON within the InformaCast ecosystem, we invite your technical team to validate the following criteria:

1. **Multicast Packet Reception:**
   - [ ] Verify BEACON correctly binds to the assigned multicast group.
   - [ ] Verify BEACON parses the broadcast payload and triggers the alert UI in < 1 second.

2. **Priority Override:**
   - [ ] Verify that an incoming InformaCast alert preempts all existing on-screen content, regardless of the current slides or AI weather generation state.
   
3. **SRP Compliance (I Love U Guys):**
   - [ ] Verify semantic mapping of alert types (Lockdown, Secure, Hold, Evacuate, Shelter) to their corresponding high-contrast colors and terminologies.

4. **Clear / All-Clear Resolution:**
   - [ ] Verify that sending a resolution packet (e.g., `{"type": "ALL CLEAR"}`) successfully dismisses the alert and restores normal signage operations.

## Next Steps
We are prepared to adjust our local multicast listeners and/or SIP/SLP discovery methods to match Singlewire's exact proprietary payload specifications if necessary. Please provide the technical integration packet or schedule a sandbox review with our engineering team.