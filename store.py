"""Persistence, namespaced per business slug. Uses Firestore if Firebase creds are available
(collections `rubi_<slug>_*`, never mixes with Clo's data); otherwise falls back to in-memory so the
demo runs with zero DB setup. Every business's data is fully isolated , a message to Rubirosa's line
never shows up on Giorgio's dashboard.
"""
import logging
import os
import threading
import time
import uuid

logger = logging.getLogger("rubi.store")

_db = None
_USE_FS = False
_lock = threading.Lock()
_mem = {}   # slug -> {"reservations": [], "inquiries": [], "messages": []}
_mem_docs = {}   # collection -> {doc_id: dict}  (keyed-doc fallback, used by context.py)


def _init():
    global _db, _USE_FS
    if _db is not None or _USE_FS:
        return
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore
        if not firebase_admin._apps:
            cp = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
            cred = credentials.Certificate(cp) if cp else credentials.ApplicationDefault()
            firebase_admin.initialize_app(cred)
        _db = firestore.client()
        _USE_FS = True
        logger.info("store: using Firestore")
    except Exception:
        logger.warning("store: no Firebase creds , using in-memory (fine for a demo)")


def db():
    """The Firestore client, or None when running in-memory (demo mode)."""
    _init()
    return _db if _USE_FS else None


def mem_doc(collection: str, doc_id: str) -> dict:
    """In-memory keyed-doc fallback: a live dict for {collection}/{doc_id}."""
    with _lock:
        return _mem_docs.setdefault(collection, {}).setdefault(doc_id, {})


def _now():
    return time.time()


def _col(slug, kind):
    return f"rubi_{slug}_{kind}"


def _bucket(slug, kind):
    return _mem.setdefault(slug, {}).setdefault(kind, [])


def _add(slug, kind, doc):
    _init()
    if _USE_FS:
        ref = _db.collection(_col(slug, kind))
        (ref.document(doc["id"]).set(doc) if doc.get("id") else ref.add(doc))
    else:
        with _lock:
            _bucket(slug, kind).append(doc)


def add_reservation(slug, customer_phone, name, party_size, date, time_str, notes=""):
    doc = {"id": uuid.uuid4().hex[:8].upper(), "customer_phone": customer_phone, "name": name,
           "party_size": party_size, "date": date, "time": time_str, "notes": notes,
           "status": "confirmed", "created_at": _now()}
    _add(slug, "reservations", doc)
    return doc["id"]


def cancel_reservation(slug, customer_phone, name):
    _init()
    if _USE_FS:
        q = (_db.collection(_col(slug, "reservations")).where("customer_phone", "==", customer_phone)
             .where("status", "==", "confirmed").limit(5).stream())
        for d in q:
            r = d.to_dict()
            if not name or name.lower() in r.get("name", "").lower():
                d.reference.update({"status": "cancelled", "updated_at": _now()})
                return r["id"]
        return None
    with _lock:
        for r in reversed(_bucket(slug, "reservations")):
            if r["customer_phone"] == customer_phone and r["status"] == "confirmed" \
                    and (not name or name.lower() in r["name"].lower()):
                r["status"] = "cancelled"
                return r["id"]
    return None


def add_inquiry(slug, customer_phone, name, question, notes=""):
    doc = {"id": uuid.uuid4().hex[:8].upper(), "customer_phone": customer_phone, "name": name,
           "question": question, "notes": notes, "created_at": _now()}
    _add(slug, "inquiries", doc)
    return doc["id"]


def log_message(slug, customer_phone, role, text):
    _add(slug, "messages", {"id": uuid.uuid4().hex[:10], "customer_phone": customer_phone,
                            "role": role, "text": text, "created_at": _now()})


def get_history(slug, customer_phone, limit=12):
    _init()
    if _USE_FS:
        from firebase_admin import firestore
        q = (_db.collection(_col(slug, "messages")).where("customer_phone", "==", customer_phone)
             .order_by("created_at", direction=firestore.Query.DESCENDING).limit(limit).stream())
        return list(reversed([d.to_dict() for d in q]))
    with _lock:
        msgs = [m for m in _bucket(slug, "messages") if m["customer_phone"] == customer_phone]
    return msgs[-limit:]


def customers_for(tenant, limit=200):
    """Known end-customers for a tenant (from the identity layer), newest activity first."""
    _init()
    out = []
    if _USE_FS:
        try:
            for d in (_db.collection("rubi_customers").where("tenant", "==", tenant)
                      .limit(limit).stream()):
                out.append(d.to_dict() or {})
        except Exception:
            logger.exception("customers query failed")
    else:
        with _lock:
            out = [dict(v) for v in _mem_docs.get("rubi_customers", {}).values()
                   if v.get("tenant") == tenant]
    out.sort(key=lambda c: c.get("last_seen") or 0, reverse=True)
    return out[:limit]


def dashboard_data(slug):
    _init()
    if _USE_FS:
        from firebase_admin import firestore

        def recent(kind, n):
            return [d.to_dict() for d in _db.collection(_col(slug, kind))
                    .order_by("created_at", direction=firestore.Query.DESCENDING).limit(n).stream()]
        return {"reservations": recent("reservations", 50),
                "inquiries": recent("inquiries", 50),
                "messages": recent("messages", 100)}
    with _lock:
        srt = lambda kind, n: sorted(_bucket(slug, kind), key=lambda x: x["created_at"], reverse=True)[:n]
        return {"reservations": srt("reservations", 50),
                "inquiries": srt("inquiries", 50), "messages": srt("messages", 100)}
