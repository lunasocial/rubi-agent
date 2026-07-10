# Rubirosa Receptionist , demo

A standalone AI receptionist for one restaurant. Customers text the business's line; the agent takes
reservations, answers menu/hours questions, and logs inquiries. A live dashboard shows the owner
everything in real time. Fully isolated from Clo (separate service, separate `rubi_*` Firestore data).

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # fill in GOOGLE_API_KEY, LINQ_API_KEY, LINQ_FROM, creds
uvicorn server:app --host 0.0.0.0 --port 8090
```

Point the Linq webhook for `+1 904 874 1368` at `https://<public-host>/webhook`.

Dashboard: open `http://localhost:8090/` (auto-refreshes every 4s).

## Config

Everything the agent may state as fact lives in `config.py` (menu, hours, address, policy). Prices/hours
are seeded from public info , confirm with the owner. `owner_phone` enables escalation.

## Files

- `config.py` , the business config (the agent's ground truth)
- `agent.py` , Gemini agent + tools (reserve / cancel / log inquiry / escalate)
- `linq.py` , send + inbound parse for the Linq line
- `store.py` , Firestore persistence (`rubi_*` collections)
- `server.py` , webhook + dashboard API
- `dashboard/index.html` , the live owner dashboard
