# Business Dashboard API (v1)

Base: `https://agent.contextualintelligence.co/rubi/api/v1`

## Auth
`Authorization: Bearer <token>` on every request.
- **Service token** (`RUBI_API_TOKEN`, ask backend): full access to all tenants. Keep it server-side ,
  proxy from your backend rather than shipping it to the browser.
- **Firebase ID token**: a dashboard user sees only tenants whose `owners` list contains their
  Firebase uid or email (set at provisioning).

Unauthenticated → 401. Authenticated but not an owner of that tenant → 403.

CORS: same-origin/proxy by default; direct browser calls need your origin added to
`RUBI_CORS_ORIGINS` (ask backend).

## Endpoints

### GET /tenants
Tenants the caller may access.
```json
{"tenants": [{"id": "giorgios", "name": "Giorgio's of Gramercy", "type": "restaurant",
              "neighborhood": "Gramercy, Manhattan", "line": ""}]}
```

### GET /tenants/{id}/data
The live feed , newest first: `messages` (100: `role` user|assistant, `text`, `customer_phone`,
`created_at` unix), `reservations` (50: name, party_size, date, time, status), `inquiries` (50),
plus `business`, `type`.

### GET /tenants/{id}/stats
```json
{"conversations": 12, "messages": 140, "inbound": 78, "bookings": 9, "inquiries": 4,
 "converted_customers": 7, "conversion_rate": 0.583,
 "messages_7d": 96, "customers_7d": 8,
 "latency": {"p50_ms": 2100, "p95_ms": 4800, "samples": 61},
 "today": {"inbound": 22, "reply": 21, "block": 1, "missed_call": 0}}
```
`conversion_rate` = customers who texted and ended up with a live booking / all texting customers.
`latency` is agent turn time (inbound → reply generated), today's rolling sample.

### GET /tenants/{id}/customers
Known end-customers (identity layer), newest activity first: `phone`, `name?`, `first_seen`,
`last_seen`, `messages`, `consent` (`ok` | `stopped`).

## Notes
- Poll `data`/`stats` (the demo dashboard polls every 4s); no websocket yet.
- All timestamps are unix seconds.
- Phone numbers are raw E.164 , mask in the UI (`+1•••4567`).
- Pricing/billing fields don't exist yet , plan model TBD.
