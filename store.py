"""Persistence. Uses Firestore if Firebase creds are available (separate `rubi_*` namespace, never
mixes with Clo's data); otherwise falls back to in-memory so the demo runs with zero DB setup.
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
_mem = {"rubi_reservations": [], "rubi_inquiries": [], "rubi_messages": []}


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


def _now():
    return time.time()


def _add(col, doc):
    _init()
    if _USE_FS:
        ref = _db.collection(col)
        (ref.document(doc["id"]).set(doc) if doc.get("id") else ref.add(doc))
    else:
        with _lock:
            _mem[col].append(doc)


def add_reservation(customer_phone, name, party_size, date, time_str, notes=""):
    doc = {"id": uuid.uuid4().hex[:8].upper(), "customer_phone": customer_phone, "name": name,
           "party_size": party_size, "date": date, "time": time_str, "notes": notes,
           "status": "confirmed", "created_at": _now()}
    _add("rubi_reservations", doc)
    return doc["id"]


def cancel_reservation(customer_phone, name):
    _init()
    if _USE_FS:
        from firebase_admin import firestore
        q = (_db.collection("rubi_reservations").where("customer_phone", "==", customer_phone)
             .where("status", "==", "confirmed").limit(5).stream())
        for d in q:
            r = d.to_dict()
            if not name or name.lower() in r.get("name", "").lower():
                d.reference.update({"status": "cancelled", "updated_at": _now()})
                return r["id"]
        return None
    with _lock:
        for r in reversed(_mem["rubi_reservations"]):
            if r["customer_phone"] == customer_phone and r["status"] == "confirmed" \
                    and (not name or name.lower() in r["name"].lower()):
                r["status"] = "cancelled"
                return r["id"]
    return None


def add_inquiry(customer_phone, name, question, notes=""):
    doc = {"id": uuid.uuid4().hex[:8].upper(), "customer_phone": customer_phone, "name": name,
           "question": question, "notes": notes, "created_at": _now()}
    _add("rubi_inquiries", doc)
    return doc["id"]


def log_message(customer_phone, role, text):
    _add("rubi_messages", {"id": uuid.uuid4().hex[:10], "customer_phone": customer_phone,
                           "role": role, "text": text, "created_at": _now()})


def get_history(customer_phone, limit=12):
    _init()
    if _USE_FS:
        from firebase_admin import firestore
        q = (_db.collection("rubi_messages").where("customer_phone", "==", customer_phone)
             .order_by("created_at", direction=firestore.Query.DESCENDING).limit(limit).stream())
        return list(reversed([d.to_dict() for d in q]))
    with _lock:
        msgs = [m for m in _mem["rubi_messages"] if m["customer_phone"] == customer_phone]
    return msgs[-limit:]


def dashboard_data():
    _init()
    if _USE_FS:
        from firebase_admin import firestore

        def recent(col, n):
            return [d.to_dict() for d in _db.collection(col)
                    .order_by("created_at", direction=firestore.Query.DESCENDING).limit(n).stream()]
        return {"reservations": recent("rubi_reservations", 50),
                "inquiries": recent("rubi_inquiries", 50),
                "messages": recent("rubi_messages", 100)}
    with _lock:
        srt = lambda col, n: sorted(_mem[col], key=lambda x: x["created_at"], reverse=True)[:n]
        return {"reservations": srt("rubi_reservations", 50),
                "inquiries": srt("rubi_inquiries", 50), "messages": srt("rubi_messages", 100)}
