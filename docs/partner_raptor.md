ave # 🦖 BEACON Integration Request: Raptor Connect

**To:** Raptor Technologies Partnerships / Integration Team  
**From:** Morrow Edge | BEACON IT Administration  
**Subject:** Request for Endpoint Validation and Raptor Connect Integration  

## Overview
The **Morrow Edge | BEACON Dashboard** is an enterprise-grade digital signage and environmental awareness platform utilized across local school districts. 

We are formally requesting validation to operate as a "Raptor Ready" digital signage endpoint. Our system has been explicitly architected to ingest Raptor Alert payloads and instantly deploy full-screen visual takeovers in accordance with the "I Love U Guys" Standard Response Protocol (SRP).

## Technical Architecture

BEACON exposes an inbound webhook designed to map directly to the Raptor Connect API.

- **Integration Type:** Inbound Webhook (Raptor -> BEACON)
- **Endpoint URL:** Provided securely by the deployment administrator (e.g., `https://beacon.district.edu/api/eap/webhook`)
- **Method:** `POST`
- **Content-Type:** `application/json`

### Supported Payload Mapping
BEACON natively parses the `IncidentType` parameter passed by Raptor Alert to trigger the corresponding SRP color codes and visual takeovers.

```json
{
  "IncidentType": "LOCKDOWN",
  "message": "Locks, Lights, Out of Sight. Initiated by Raptor Alert.",
  "Location": "Main Campus"
}
```

## Verification & Certification Checklist

To certify BEACON as a compatible endpoint, we invite your integration engineers to verify the following capabilities against our test environment:

1. **Webhook Ingestion:** 
   - [ ] Verify BEACON successfully receives HTTP POST payloads from Raptor Connect.
   - [ ] Verify HTTP 200 OK acknowledgment is returned to Raptor.

2. **SRP Visual Compliance:** 
   - [ ] Verify `IncidentType: LOCKDOWN` triggers a Red full-screen takeover.
   - [ ] Verify `IncidentType: SECURE` triggers an Orange full-screen takeover.
   - [ ] Verify `IncidentType: HOLD` triggers a Purple full-screen takeover.
   - [ ] Verify `IncidentType: EVACUATE` triggers an Orange/Red full-screen takeover.
   - [ ] Verify `IncidentType: SHELTER` triggers a Blue full-screen takeover.

## Next Steps
Please provide the necessary testing credentials or sandbox environment access so our IT staff can finalize the Webhook URL registration within the Raptor Connect portal. We look forward to partnering with you to enhance school safety.