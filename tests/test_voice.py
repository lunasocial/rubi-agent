"""Voice missed-call tests: TwiML dial, outcome handling, text-back + consent. Hermetic:

    python3 tests/test_voice.py
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
os.environ["RUBI_MISSED_CALL_ENABLED"] = "1"

import context
context._redis_tried = True
context._redis = None

import businesses
import governance
import linq
import store
import voice

SENT = []


async def fake_send(recipient, text):
    SENT.append((recipient, text))
    return True

linq.send = fake_send
voice.linq.send = fake_send


def test_incoming_twiml():
    businesses.BUSINESSES["lenlen"]["forward_phone"] = "+16464298220"
    x = voice.incoming_twiml("lenlen", "https://x.co/rubi")
    assert '<Dial timeout="20" action="https://x.co/rubi/voice/after?tenant=lenlen"' in x, x
    assert "<Number>+16464298220</Number>" in x
    # no forward number -> straight to the missed-call path
    del businesses.BUSINESSES["lenlen"]["forward_phone"]
    businesses.BUSINESSES["lenlen"]["phone"] = ""
    x2 = voice.incoming_twiml("lenlen", "https://x.co/rubi")
    assert "<Redirect" in x2 and "voice/after?tenant=lenlen" in x2, x2
    businesses.BUSINESSES["lenlen"]["phone"] = "(646) 429-8220"
    print("ok: incoming TwiML , dial with action, redirect fallback")


def test_after_dial():
    async def flow():
        # answered -> quiet hangup, no text
        x = await voice.after_dial("lenlen", {"DialCallStatus": "completed", "From": "+15551112222"})
        assert "<Hangup/>" in x and not SENT

        # missed -> text-back from the tenant's line + spoken notice + logged
        x = await voice.after_dial("lenlen", {"DialCallStatus": "no-answer", "From": "+15551112222"})
        assert "sent you a text" in x, x
        assert SENT and "missed your call at LenLen" in SENT[-1][1]
        hist = store.get_history("lenlen", "+15551112222")
        assert any("missed your call" in m.get("text", "") for m in hist)

        # opted-out caller -> no text, polite say only
        governance._set_consent("lenlen", "+15553334444", "stopped")
        n = len(SENT)
        x = await voice.after_dial("lenlen", {"DialCallStatus": "busy", "From": "+15553334444"})
        assert len(SENT) == n and "try again shortly" in x

        # flag off -> no text
        os.environ["RUBI_MISSED_CALL_ENABLED"] = "0"
        n = len(SENT)
        await voice.after_dial("lenlen", {"DialCallStatus": "no-answer", "From": "+15555556666"})
        assert len(SENT) == n
        os.environ["RUBI_MISSED_CALL_ENABLED"] = "1"
    asyncio.run(flow())
    print("ok: after-dial , answered/missed/suppressed/flag-off")


def test_tenant_resolution_and_routes():
    businesses.BUSINESSES["lenlen"]["voice_number"] = "+19998887777"
    assert voice.tenant_for_call({"To": "+19998887777"}) == "lenlen"
    assert voice.tenant_for_call({"To": "+10000000000"}) == businesses.active_slug()
    del businesses.BUSINESSES["lenlen"]["voice_number"]

    from fastapi.testclient import TestClient
    import server
    server.voice.linq.send = fake_send
    c = TestClient(server.app)
    r = c.post("/voice/incoming", content="From=%2B15551112222&To=%2B19998887777",
               headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 200 and "<Response>" in r.text, r.text
    r = c.post("/voice/after?tenant=lenlen",
               content="DialCallStatus=no-answer&From=%2B15559998888",
               headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 200 and "sent you a text" in r.text, r.text
    assert any(p == "+15559998888" for p, _ in SENT)
    print("ok: tenant resolution by DID + live routes")


if __name__ == "__main__":
    test_incoming_twiml()
    test_after_dial()
    test_tenant_resolution_and_routes()
    print("\nALL VOICE TESTS PASSED")
