"""Tenant-scoped customer context: taste profile + semantic memory + engagement style.

Ported from the proven Luna memory engine (contact_memory / memory_store / engagement_profile),
adapted for multi-tenant: every key is {tenant}:{phone}, data lives in rubi-owned collections
(`rubi_customers`, `rubi_profiles`, `rubi_memories`) so nothing touches Clo's namespaces, and Redis
(optional, own DB) only caches , Firestore is the source of truth. Behind RUBI_CONTEXT_ENABLED so the
demo path is unchanged until we flip it on. Everything degrades gracefully: no Redis -> in-process
cache, no Firebase -> in-memory (demo), embed failure -> skip, never raise into the reply path.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import threading
import time

import store

logger = logging.getLogger("rubi.context")

EMBED_MODEL = os.getenv("EMBED_MODEL", "gemini-embedding-001")
REFLECT_MODEL = os.getenv("MEMORY_REFLECT_MODEL", os.getenv("RUBI_MODEL", "gemini-3-flash-preview"))
_VEC_TTL = 600
_MAX_ITEMS = 300
_MIN_SIM = float(os.getenv("MEMORY_MIN_SIM", "0.55"))
_MIN_CHARS = 24
_DEDUP_SIM = float(os.getenv("MEMORY_DEDUP_SIM", "0.92"))
_MAX_AGE_DAYS = float(os.getenv("MEMORY_MAX_AGE_DAYS", "540"))
_REFLECT_EVERY = int(os.getenv("MEMORY_REFLECT_EVERY", "8"))
_MAX_PER_FIELD = 25

_LIST_FIELDS = ("likes", "dislikes", "dietary", "preferences", "occasions", "notes")
_LABELS = {
    "likes": "Likes",
    "dislikes": "Dislikes / avoid",
    "dietary": "Dietary",
    "preferences": "Preferences",
    "occasions": "Occasions / dates",
    "notes": "Other notes",
}


def enabled() -> bool:
    return os.getenv("RUBI_CONTEXT_ENABLED", "0") == "1"


def _key(tenant: str, phone: str) -> str:
    return f"{tenant}:{phone}"


def _doc_id(tenant: str, phone: str) -> str:
    return f"{tenant}__{phone}".replace("/", "_")


# ---- redis (optional cache; in-process fallback) -----------------------------
_redis = None
_redis_tried = False
_fallback: dict = {}          # key -> (value, expires_at|None)
_flock = threading.Lock()


async def _r():
    global _redis, _redis_tried
    if not _redis_tried:
        _redis_tried = True
        try:
            import redis.asyncio as aioredis
            _redis = aioredis.from_url(
                os.getenv("RUBI_REDIS_URL", "redis://localhost:6379/2"), decode_responses=True)
            await _redis.ping()
            logger.info("context: redis cache connected")
        except Exception:
            _redis = None
            logger.info("context: no redis, using in-process cache")
    return _redis


async def _cache_get(key: str):
    r = await _r()
    if r is not None:
        try:
            return await r.get(key)
        except Exception:
            pass
    with _flock:
        v = _fallback.get(key)
        if v and (v[1] is None or v[1] > time.time()):
            return v[0]
    return None


async def _cache_set(key: str, val: str, ex: int | None = None):
    r = await _r()
    if r is not None:
        try:
            await r.set(key, val, ex=ex)
            return
        except Exception:
            pass
    with _flock:
        _fallback[key] = (val, time.time() + ex if ex else None)


async def _cache_del(key: str):
    r = await _r()
    if r is not None:
        try:
            await r.delete(key)
            return
        except Exception:
            pass
    with _flock:
        _fallback.pop(key, None)


async def _cache_incr(key: str, ttl: int | None = None) -> int:
    r = await _r()
    if r is not None:
        try:
            n = await r.incr(key)
            if ttl:
                await r.expire(key, ttl)
            return int(n)
        except Exception:
            pass
    with _flock:
        v = _fallback.get(key)
        n = (int(v[0]) if v and (v[1] is None or v[1] > time.time()) else 0) + 1
        _fallback[key] = (str(n), time.time() + ttl if ttl else None)
        return n


# ---- customer entity (identity layer v1) -------------------------------------
def touch_customer(tenant: str, phone: str, name: str = "") -> None:
    """Upsert the canonical end-customer record for this tenant. Isolated per tenant by design;
    global_person_id stays null until a future consented federation feature."""
    did = _doc_id(tenant, phone)
    now = time.time()

    def _w():
        db = store.db()
        if db is not None:
            ref = db.collection("rubi_customers").document(did)
            cur = ref.get().to_dict() or {}
            doc = {"tenant": tenant, "phone": phone, "last_seen": now,
                   "messages": int(cur.get("messages") or 0) + 1}
            if not cur:
                doc.update({"first_seen": now, "consent": "ok", "global_person_id": None})
            if name and not cur.get("name"):
                doc["name"] = name.strip()
            ref.set(doc, merge=True)
        else:
            cur = store.mem_doc("rubi_customers", did)
            cur.update({"tenant": tenant, "phone": phone, "last_seen": now,
                        "messages": int(cur.get("messages") or 0) + 1})
            cur.setdefault("first_seen", now)
            cur.setdefault("consent", "ok")
            cur.setdefault("global_person_id", None)
            if name and not cur.get("name"):
                cur["name"] = name.strip()
    try:
        _w()
    except Exception:
        logger.debug("touch_customer failed", exc_info=True)


# ---- taste profile (from contact_memory) --------------------------------------
def _merge_list(existing, new_items) -> list:
    seen = {x.lower(): x for x in (existing or [])}
    for item in new_items:
        item = (item or "").strip()
        if item and item.lower() not in seen:
            seen[item.lower()] = item
    return list(seen.values())[-_MAX_PER_FIELD:]


def read_profile(tenant: str, phone: str) -> dict:
    did = _doc_id(tenant, phone)
    try:
        db = store.db()
        if db is not None:
            doc = db.collection("rubi_profiles").document(did).get()
            return doc.to_dict() if doc.exists else {}
        return dict(store.mem_doc("rubi_profiles", did))
    except Exception:
        return {}


def write_profile(tenant: str, phone: str, updates: dict, name: str = "") -> None:
    did = _doc_id(tenant, phone)
    try:
        cur = read_profile(tenant, phone)
        merged: dict = {}
        for f in _LIST_FIELDS:
            vals = updates.get(f) or []
            if isinstance(vals, str):
                vals = [v.strip() for v in vals.split(",") if v.strip()]
            if vals:
                merged[f] = _merge_list(cur.get(f), vals)
        if name and not cur.get("name"):
            merged["name"] = name.strip()
        if not merged:
            return
        db = store.db()
        if db is not None:
            db.collection("rubi_profiles").document(did).set(merged, merge=True)
        else:
            store.mem_doc("rubi_profiles", did).update(merged)
    except Exception:
        logger.debug("write_profile failed", exc_info=True)


def format_profile(profile: dict) -> str:
    if not profile:
        return ""
    parts = []
    if profile.get("name"):
        parts.append(f"Name: {profile['name']}")
    for f, label in _LABELS.items():
        val = profile.get(f)
        if val:
            parts.append(f"{label}: {', '.join(val)}")
    return "\n".join(parts)


# ---- semantic memory (from memory_store) ---------------------------------------
async def embed(text: str):
    def _e():
        from google import genai
        key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        client = genai.Client(api_key=key)
        r = client.models.embed_content(model=EMBED_MODEL, contents=text)
        embs = getattr(r, "embeddings", None)
        if embs:
            return list(embs[0].values)
        emb = getattr(r, "embedding", None)
        return list(getattr(emb, "values", []) or [])
    return await asyncio.to_thread(_e)


_SALIENT = ("i ", "i'm", "im ", "my ", "we ", "love", "hate", "always", "usually", "never",
            "allergic", "vegan", "vegetarian", "gluten", "favorite", "fav ", "birthday",
            "anniversary", "prefer", "obsessed", "can't do", "cant do", "into ", "regular",
            "every week", "usual")


def is_worth_remembering(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < _MIN_CHARS:
        return False
    tl = t.lower()
    return any(s in tl for s in _SALIENT)


def _cos(a, b) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def _recency_weight(ts: float, now: float) -> float:
    if not ts:
        return 1.0
    age_days = max(0.0, (now - ts) / 86400.0)
    return max(0.7, 1.0 - 0.0006 * age_days)


_mem_fallback: dict = {}       # key -> [ {text, emb, kind, ts} ]  (no-Firebase demo mode)


def _mem_bucket(key: str) -> list:
    return _mem_fallback.setdefault(key, [])


async def _load(key: str):
    c = await _cache_get("mem:vec:" + key)
    if c is not None:
        try:
            return json.loads(c)
        except Exception:
            pass

    def _read():
        db = store.db()
        if db is None:
            return [dict(it) for it in _mem_bucket(key)]
        items = []
        for d in db.collection("rubi_memories").where("key", "==", key).limit(_MAX_ITEMS).stream():
            x = d.to_dict() or {}
            if x.get("text") and x.get("embedding"):
                items.append({"text": x["text"], "emb": x["embedding"], "kind": x.get("kind", ""),
                              "ts": float(x.get("ts") or 0)})
        return items
    try:
        items = await asyncio.to_thread(_read)
    except Exception:
        return []
    try:
        await _cache_set("mem:vec:" + key, json.dumps(items), ex=_VEC_TTL)
    except Exception:
        pass
    return items


async def remember(tenant: str, phone: str, text: str, kind: str = "episode") -> None:
    key = _key(tenant, phone)
    text = (text or "").strip()
    if len(text) < _MIN_CHARS and kind != "insight":
        return
    try:
        emb = await embed(text)
    except Exception:
        logger.debug("embed (remember) failed", exc_info=True)
        return
    if not emb:
        return
    existing = await _load(key)
    if any(_cos(emb, it["emb"]) >= _DEDUP_SIM for it in existing):
        return

    def _w():
        db = store.db()
        doc = {"key": key, "tenant": tenant, "text": text, "kind": kind, "embedding": emb,
               "ts": time.time()}
        if db is not None:
            db.collection("rubi_memories").add(doc)
        else:
            _mem_bucket(key).append({"text": text, "emb": emb, "kind": kind, "ts": doc["ts"]})
    try:
        await asyncio.to_thread(_w)
    except Exception:
        logger.debug("memory write failed", exc_info=True)
        return
    await _cache_del("mem:vec:" + key)
    if len(existing) + 1 > _MAX_ITEMS:
        try:
            await consolidate(tenant, phone)
        except Exception:
            pass
    if kind != "insight":
        try:
            n = await _cache_incr("mem:reflect:" + key)
            if n >= _REFLECT_EVERY:
                await _cache_set("mem:reflect:" + key, "0")
                asyncio.create_task(reflect(tenant, phone))
        except Exception:
            pass


async def reflect(tenant: str, phone: str) -> int:
    """Synthesize a few durable insights from recent raw episodes (stored as kind='insight')."""
    key = _key(tenant, phone)
    items = await _load(key)
    episodes = [it for it in items if it.get("kind") != "insight"]
    if len(episodes) < _REFLECT_EVERY:
        return 0
    recent = sorted(episodes, key=lambda it: -(it.get("ts") or 0))[:25]
    facts = "\n".join("- " + it["text"] for it in recent if it.get("text"))
    prompt = ("Here are things a customer told a business's receptionist over time. Write 1 to 3 "
              "SHORT, durable insights about who they are , their taste, habits, what they value "
              "(e.g. 'regular weekend lunch, usually party of 2', 'always asks about gluten-free'). "
              "One per line, no numbering, no preamble. Only insights you're genuinely confident "
              "about.\n\n" + facts)

    def _gen():
        from google import genai
        client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))
        r = client.models.generate_content(model=REFLECT_MODEL, contents=prompt)
        return getattr(r, "text", "") or ""
    try:
        out = await asyncio.to_thread(_gen)
    except Exception:
        return 0
    n = 0
    for line in out.splitlines():
        insight = line.strip().lstrip("-•*0123456789. ").strip()
        if len(insight) >= 12 and n < 3:
            await remember(tenant, phone, insight, kind="insight")
            n += 1
    return n


async def consolidate(tenant: str, phone: str, now: float | None = None) -> int:
    """Age out memories older than the max age and cap the corpus to the newest _MAX_ITEMS."""
    key = _key(tenant, phone)
    now = now or time.time()

    def _prune():
        db = store.db()
        if db is None:
            bucket = _mem_bucket(key)
            kept = sorted((it for it in bucket if (now - it["ts"]) <= _MAX_AGE_DAYS * 86400),
                          key=lambda it: -it["ts"])[:_MAX_ITEMS]
            dropped = len(bucket) - len(kept)
            _mem_fallback[key] = list(reversed(kept))
            return dropped
        items = []
        for d in db.collection("rubi_memories").where("key", "==", key).stream():
            x = d.to_dict() or {}
            items.append({"id": d.id, "ts": float(x.get("ts") or 0)})
        drop = {it["id"] for it in items if it["ts"] and (now - it["ts"]) > _MAX_AGE_DAYS * 86400}
        kept = sorted((it for it in items if it["id"] not in drop), key=lambda it: -it["ts"])
        for it in kept[_MAX_ITEMS:]:
            drop.add(it["id"])
        col = db.collection("rubi_memories")
        for i in drop:
            try:
                col.document(i).delete()
            except Exception:
                pass
        return len(drop)
    try:
        pruned = await asyncio.to_thread(_prune)
    except Exception:
        return 0
    if pruned:
        await _cache_del("mem:vec:" + key)
    return pruned


async def recall(tenant: str, phone: str, query: str, k: int = 3):
    key = _key(tenant, phone)
    if not (query or "").strip():
        return []
    items = await _load(key)
    if not items:
        return []
    try:
        q = await embed(query)
    except Exception:
        return []
    if not q:
        return []
    now = time.time()
    scored = [(_cos(q, it["emb"]) * _recency_weight(it.get("ts") or 0, now), it["text"])
              for it in items]
    scored.sort(key=lambda s: -s[0])
    return [t for s, t in scored[:k] if s >= _MIN_SIM]


# ---- engagement style (from engagement_profile) --------------------------------
_TTL_ENG = 30 * 86400
_DECISIVE = ("just pick", "you pick", "you choose", "you decide", "surprise me", "whatever you",
             "your call", "up to you", "whatever works")
_OPTIONS = ("what are my options", "give me options", "what else", "other options", "a few options",
            "some options", "options?", "anything else")


def classify_signals(text: str):
    t = (text or "").lower()
    out = []
    if any(p in t for p in _DECISIVE):
        out.append("decisive")
    if any(p in t for p in _OPTIONS):
        out.append("wants_options")
    return out


async def record_signal(tenant: str, phone: str, signal: str) -> None:
    try:
        await _cache_incr(f"profile:{signal}:{_key(tenant, phone)}", ttl=_TTL_ENG)
    except Exception:
        pass


async def _signal_count(tenant: str, phone: str, signal: str) -> int:
    try:
        return int(await _cache_get(f"profile:{signal}:{_key(tenant, phone)}") or 0)
    except Exception:
        return 0


async def style_line(tenant: str, phone: str) -> str:
    """One short instruction on how this customer likes to be handled ('' until there's signal)."""
    decisive = await _signal_count(tenant, phone, "decisive")
    options = await _signal_count(tenant, phone, "wants_options")
    total = decisive + options
    if total == 0:
        return ""
    ratio = decisive / total
    if ratio >= 0.6:
        return "HOW THEY LIKE TO BE HANDLED: just make a confident suggestion, don't list options."
    if ratio <= 0.4:
        return "HOW THEY LIKE TO BE HANDLED: offer a couple of options, they like choosing."
    return ""


# ---- turn-level API (what agent.py calls) ---------------------------------------
async def assemble(tenant: str, phone: str, latest_text: str) -> str:
    """The context block injected into the system prompt. Empty string when nothing is known."""
    if not enabled():
        return ""
    try:
        profile_t = asyncio.to_thread(read_profile, tenant, phone)
        recall_t = recall(tenant, phone, latest_text)
        style_t = style_line(tenant, phone)
        profile, mems, style = await asyncio.gather(profile_t, recall_t, style_t,
                                                    return_exceptions=True)
        parts = []
        p = format_profile(profile) if isinstance(profile, dict) else ""
        if p:
            parts.append("WHAT WE KNOW ABOUT THIS CUSTOMER (from past interactions):\n" + p)
        if isinstance(mems, list) and mems:
            parts.append("THINGS THEY'VE TOLD US BEFORE:\n" + "\n".join(f"- {m}" for m in mems))
        if isinstance(style, str) and style:
            parts.append(style)
        if not parts:
            return ""
        parts.append("Use this naturally (greet them by name, honor preferences) , never recite "
                     "it back or mention you keep notes.")
        return "\n\n".join(parts)
    except Exception:
        logger.debug("assemble failed", exc_info=True)
        return ""


async def after_turn(tenant: str, phone: str, text: str) -> None:
    """Post-reply write path: identity touch, engagement signals, salient-fact capture."""
    if not enabled():
        return
    try:
        await asyncio.to_thread(touch_customer, tenant, phone)
        for sig in classify_signals(text):
            await record_signal(tenant, phone, sig)
        if is_worth_remembering(text):
            await remember(tenant, phone, text)
    except Exception:
        logger.debug("after_turn failed", exc_info=True)
