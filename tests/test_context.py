"""Context-engine tests: flag-off parity, tenant isolation, memory compounding.
Runs hermetic (stubbed genai, deterministic embeddings, in-memory store, no redis):

    python3 tests/test_context.py
"""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# stub google.genai so agent imports without the SDK (box has the real one)
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

import agent
import businesses
import context

# hermetic: never touch a real local redis , cache state must not leak between runs
context._redis_tried = True
context._redis = None

PHONE = "+15551234567"
FAKE = {
    "i'm allergic to peanuts and i love tiramisu": [1.0, 0.0, 0.0],
    "any dessert recommendations?": [0.95, 0.05, 0.0],
    "we usually come every friday around 7": [0.0, 1.0, 0.0],
}


async def fake_embed(text):
    return FAKE.get(text, [0.0, 0.0, 1.0])


def test_flag_off():
    assert not context.enabled()
    cfg = businesses.get("rubirosa")
    names = [t.__name__ for t in agent._tools("rubirosa", PHONE, cfg)]
    assert "remember_about_customer" not in names, names
    assert agent._system(cfg, "today") == agent._system(cfg, "today", "")
    assert "CUSTOMER" not in agent._system(cfg, "today")
    assert asyncio.run(context.assemble("rubirosa", PHONE, "hi")) == ""
    print("ok: flag off , tools/prompt identical, assemble empty")


def test_flag_on():
    os.environ["RUBI_CONTEXT_ENABLED"] = "1"
    context.embed = fake_embed
    assert context.enabled()

    # tool appears
    names = [t.__name__ for t in agent._tools("rubirosa", PHONE, businesses.get("rubirosa"))]
    assert "remember_about_customer" in names, names

    # profiles isolated per tenant, same phone
    context.write_profile("t1", PHONE, {"dietary": "no peanuts", "likes": "tiramisu"}, name="Maria")
    context.write_profile("t2", PHONE, {"likes": "espresso"})
    p1, p2 = context.read_profile("t1", PHONE), context.read_profile("t2", PHONE)
    assert p1.get("dietary") == ["no peanuts"] and p1.get("name") == "Maria", p1
    assert p2.get("likes") == ["espresso"] and "dietary" not in p2, p2
    print("ok: profiles isolated per tenant")

    # customer entity
    context.touch_customer("t1", PHONE, "Maria")
    import store
    c = store.mem_doc("rubi_customers", "t1__" + PHONE)
    assert c["tenant"] == "t1" and c["name"] == "Maria" and c["global_person_id"] is None, c
    assert c["messages"] >= 1 and c["consent"] == "ok"
    print("ok: customer entity upserted")

    async def mems():
        note = "i'm allergic to peanuts and i love tiramisu"
        await context.remember("t1", PHONE, note)
        await context.remember("t1", PHONE, note)          # dedup
        r1 = await context.recall("t1", PHONE, "any dessert recommendations?")
        r2 = await context.recall("t2", PHONE, "any dessert recommendations?")
        assert r1 == [note], r1                            # recalled for t1
        assert r2 == [], r2                                # invisible to t2
        assert len(context._mem_bucket("t1:" + PHONE)) == 1  # dedup held

        # salience gate
        assert context.is_worth_remembering(note)
        assert not context.is_worth_remembering("what time do you close")

        # engagement style, tenant-scoped
        for _ in range(2):
            await context.record_signal("t1", PHONE, "decisive")
        s1 = await context.style_line("t1", PHONE)
        s2 = await context.style_line("t2", PHONE)
        assert "confident" in s1 and s2 == "", (s1, s2)

        # assemble block
        block = await context.assemble("t1", PHONE, "any dessert recommendations?")
        assert "Maria" in block and "tiramisu" in block and "never recite" in block, block
        assert "CUSTOMER" in agent._system(businesses.get("rubirosa"), "today", block)

        # after_turn end-to-end (fire-and-forget path)
        await context.after_turn("t1", PHONE, "we usually come every friday around 7")
        r3 = await context.recall("t1", PHONE, "we usually come every friday around 7")
        assert any("friday" in m for m in r3), r3
    asyncio.run(mems())
    print("ok: memories isolated + dedup + recall + style + assemble + after_turn")

    os.environ["RUBI_CONTEXT_ENABLED"] = "0"
    assert asyncio.run(context.assemble("t1", PHONE, "hi")) == ""
    print("ok: flag back off , assemble empty again")


if __name__ == "__main__":
    test_flag_off()
    test_flag_on()
    print("\nALL CONTEXT TESTS PASSED")
