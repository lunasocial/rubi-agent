"""Phase 3 tests: consent (STOP/START/HELP), caps, missed-call text-back. Hermetic:

    python3 tests/test_governance.py
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

os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
os.environ["RUBI_API_TOKEN"] = "test-token"

import context
context._redis_tried = True     # hermetic: in-process cache only
context._redis = None

import governance
import linq
import server
import store

SENT = []


async def fake_send(recipient, text):
    SENT.append((recipient, text))
    return True

linq.send = fake_send
server.linq.send = fake_send


def test_intents():
    assert governance.stop_intent("STOP") and governance.stop_intent("please stop texting me")
    assert not governance.stop_intent("can't stop thinking about that pizza")
    assert governance.start_intent("START") and governance.help_intent("help")
    print("ok: intent detection")


def test_consent_flow():
    phone = "+15550001111"

    async def flow():
        # STOP -> durable opt-out + ack, agent never runs
        await server._process("rubirosa", phone, "STOP")
        assert "opted out" in SENT[-1][1]
        assert governance._get_consent("rubirosa", phone) == "stopped"
        assert store.get_history("rubirosa", phone) == []   # agent.handle never logged anything

        # while stopped: normal messages are dropped silently
        n = len(SENT)
        await server._process("rubirosa", phone, "table for 2 tonight?")
        assert len(SENT) == n and store.get_history("rubirosa", phone) == []

        # consent is per-tenant: same phone is fine at another tenant
        assert governance._get_consent("giorgios", phone) == "ok"

        # START -> opted back in
        await server._process("rubirosa", phone, "START")
        assert "opted back in" in SENT[-1][1]
        assert governance._get_consent("rubirosa", phone) == "ok"

        # HELP -> identity + opt-out line, no agent
        await server._process("rubirosa", phone, "help")
        assert "Reply STOP" in SENT[-1][1]
    asyncio.run(flow())
    print("ok: STOP/silent/START/HELP flow, per-tenant consent")


def test_cap_and_pause():
    async def flow():
        os.environ["RUBI_TENANT_DAILY_CAP"] = "2"
        assert not await governance.over_cap("captest")
        assert not await governance.over_cap("captest")
        assert await governance.over_cap("captest")          # 3rd exceeds
        assert not await governance.over_cap("othertenant")  # caps are per tenant
        os.environ["RUBI_TENANT_DAILY_CAP"] = "500"
        os.environ["RUBI_PAUSED"] = "1"
        proceed, direct = await governance.gate("rubirosa", "+15550002222", "hi")
        assert not proceed and direct == ""
        os.environ["RUBI_PAUSED"] = "0"
    asyncio.run(flow())
    print("ok: per-tenant daily cap + pause switch")


def test_missed_call():
    from fastapi.testclient import TestClient
    c = TestClient(server.app)
    h = {"Authorization": "Bearer test-token"}
    body = {"tenant": "giorgios", "caller": "+15550003333"}

    assert c.post("/event/missed_call", json=body).status_code == 401
    j = c.post("/event/missed_call", json=body, headers=h).json()
    assert j["sent"] is False and j["reason"] == "disabled"

    os.environ["RUBI_MISSED_CALL_ENABLED"] = "1"
    j = c.post("/event/missed_call", json=body, headers=h).json()
    assert j["sent"] is True, j
    assert "sorry we missed your call at Giorgio's of Gramercy" in SENT[-1][1]

    # a suppressed caller never gets a text-back
    governance._set_consent("giorgios", "+15550004444", "stopped")
    j = c.post("/event/missed_call", json={"tenant": "giorgios", "caller": "+15550004444"},
               headers=h).json()
    assert j["sent"] is False and j["reason"] == "suppressed"

    assert c.post("/event/missed_call", json={"tenant": "nope", "caller": "+1"},
                  headers=h).status_code == 400
    os.environ["RUBI_MISSED_CALL_ENABLED"] = "0"
    print("ok: missed-call text-back , auth, flag, consent, unknown tenant")


if __name__ == "__main__":
    test_intents()
    test_consent_flow()
    test_cap_and_pause()
    test_missed_call()
    print("\nALL GOVERNANCE TESTS PASSED")
