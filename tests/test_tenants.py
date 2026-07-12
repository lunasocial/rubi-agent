"""Phase 2 tests: template parity, per-tenant webhook routing, authed /api/v1.
Hermetic (stubbed genai, in-memory store):

    python3 tests/test_tenants.py
"""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

g = types.ModuleType("google")
gg = types.ModuleType("google.genai")
ggt = types.ModuleType("google.genai.types")
gg.Client = lambda **k: None
for n in ("Content", "Part", "GenerateContentConfig"):
    setattr(ggt, n, lambda **k: None)
gg.types = ggt
g.genai = gg
sys.modules.setdefault("google", g)
sys.modules.setdefault("google.genai", gg)
sys.modules.setdefault("google.genai.types", ggt)

os.environ.pop("RUBI_CONTEXT_ENABLED", None)
os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
os.environ["RUBI_API_TOKEN"] = "test-token"

import agent
import businesses
import context
import store

context._redis_tried = True     # hermetic: in-process cache only
context._redis = None


def test_restaurant_template_parity():
    s = agent._system(businesses.get("rubirosa"), "today")
    assert "a Italian-American restaurant in Nolita, Manhattan" in s
    assert "MENU (the ONLY items you may name , never invent a dish or a price we don't list):" in s
    assert "To BOOK a reservation" in s and "The reservation lands on our team's dashboard." in s
    assert s.rstrip().endswith("it's always better to log it for the team than to guess.")
    print("ok: restaurant prompt byte-parity phrases intact")


def test_hotel_template():
    cfg = {"name": "The Example", "type": "hotel", "descriptor": "boutique hotel",
           "neighborhood": "Gramercy", "address": "1 Example Ave", "known_for": "quiet luxury",
           "reservation_policy": "Book direct or via phone.", "takeout": "Room service only.",
           "hours": {"Monday": "24 hours"}, "menu": {"Rooms": ["King Suite", "Queen Deluxe"]}}
    s = agent._system(cfg, "today")
    assert "a boutique hotel in Gramercy" in s
    assert "ROOMS & OFFERINGS (the ONLY room types and amenities" in s
    assert "To BOOK a booking" in s or "To BOOK a" in s
    assert "King Suite" in s
    print("ok: hotel template renders")


def test_webhook_routing_and_api():
    from fastapi.testclient import TestClient
    import server
    c = TestClient(server.app)

    payload = {"data": {"sender_handle": {"handle": "+15559990000", "is_me": False},
                        "parts": [{"type": "text", "value": "do you have gluten free pasta"}]}}

    # per-tenant path routes to THAT tenant regardless of the active demo switch
    assert businesses.active_slug() == "rubirosa"
    r = c.post("/webhook/giorgios", json=payload)
    assert r.status_code == 200 and r.json()["ok"], r.text
    # drive the processing pipeline directly (TestClient's per-request loop drops background
    # tasks; uvicorn's persistent loop runs them , covered by the live smoke test)
    asyncio.run(server._process("giorgios", "+15559990000", "do you have gluten free pasta"))
    hist = store.get_history("giorgios", "+15559990000")
    assert any("gluten" in m.get("text", "") for m in hist), hist
    assert store.get_history("rubirosa", "+15559990000") == []
    print("ok: /webhook/{tenant} routes to that tenant, isolated from active switch")

    assert c.post("/webhook/nope", json=payload).status_code == 404

    # api auth
    assert c.get("/api/v1/tenants").status_code == 401
    h = {"Authorization": "Bearer test-token"}
    j = c.get("/api/v1/tenants", headers=h).json()
    assert any(t["id"] == "rubirosa" for t in j["tenants"]), j
    assert c.get("/api/v1/tenants/giorgios/data", headers=h).status_code == 200
    stats = c.get("/api/v1/tenants/giorgios/stats", headers=h).json()
    assert stats["messages"] >= 1 and stats["conversations"] >= 1, stats
    cust = c.get("/api/v1/tenants/giorgios/customers", headers=h).json()
    assert "customers" in cust
    assert c.get("/api/v1/tenants/nope/data", headers=h).status_code == 404
    assert c.get("/api/v1/tenants", headers={"Authorization": "Bearer wrong"}).status_code == 401
    print("ok: /api/v1 auth + data + stats + customers")

    # legacy routes untouched
    assert c.get("/health").json()["ok"]
    assert c.get("/rubirosa/api/data").status_code == 200
    print("ok: legacy demo routes unchanged")


if __name__ == "__main__":
    test_restaurant_template_parity()
    test_hotel_template()
    test_webhook_routing_and_api()
    print("\nALL TENANT TESTS PASSED")
