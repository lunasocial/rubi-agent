"""Tenant registry. Tenants live in Firestore `tenants/{slug}` (provisioned via provision.py, cached
60s); the dict below seeds the three demo restaurants and doubles as the no-Firestore fallback. Each
tenant gets its own dashboard at /<slug>/, isolated data, and (when it has a dedicated line) its own
webhook at /webhook/<slug>. The shared demo line serves whichever slug is ACTIVE (see active_slug()).

An industry template supplies the vocabulary the agent speaks per vertical; a tenant's config overrides
its template. Menus/prices are seeded from public info , owner confirms.
"""
import logging
import os
import threading
import time

logger = logging.getLogger("rubi.businesses")

TEMPLATES = {
    "restaurant": {"kind_label": "restaurant", "catalog_label": "MENU",
                   "catalog_rule": "the ONLY items you may name , never invent a dish or a price we don't list",
                   "booking_noun": "reservation", "extra": ""},
    "cafe": {"kind_label": "cafe", "catalog_label": "MENU",
             "catalog_rule": "the ONLY items you may name , never invent a drink, dish, or price we don't list",
             "booking_noun": "order", "extra": ""},
    "hotel": {"kind_label": "hotel", "catalog_label": "ROOMS & OFFERINGS",
              "catalog_rule": "the ONLY room types and amenities you may name , never invent an offering or a rate we don't list",
              "booking_noun": "booking", "extra": ""},
    "retail": {"kind_label": "store", "catalog_label": "PRODUCTS & SERVICES",
               "catalog_rule": "the ONLY products and services you may name , never invent an item or a price we don't list",
               "booking_noun": "order", "extra": ""},
}


def template(cfg: dict) -> dict:
    return TEMPLATES.get(cfg.get("type", "restaurant"), TEMPLATES["restaurant"])

BUSINESSES = {
    "rubirosa": {
        "name": "Rubirosa", "type": "restaurant", "cuisine": "Italian-American",
        "neighborhood": "Nolita, Manhattan", "address": "235 Mulberry St, New York, NY 10012",
        "known_for": "thin-crust pizza (the famous TIE DYE and Vodka pies), handmade pasta, and "
                     "reinvented Italian-American classics",
        "owner_phone": "",
        "resy": {"venue_id": "466", "city": "ny", "url_slug": "rubirosa"},
        "reservation_policy": "We take reservations for parties of 1-8. Larger groups and private events "
                              "are handled separately. Walk-ins are welcome but there's often a wait on weekends.",
        "takeout": "Yes, pickup and delivery are available for pizza and pasta.",
        "hours": {"Monday": "11:30 AM - 11:00 PM", "Tuesday": "11:30 AM - 11:00 PM",
                  "Wednesday": "11:30 AM - 11:00 PM", "Thursday": "11:30 AM - 11:00 PM",
                  "Friday": "11:30 AM - 12:00 AM", "Saturday": "11:00 AM - 12:00 AM",
                  "Sunday": "11:00 AM - 11:00 PM"},
        "menu": {
            "Pizza (Small 14\" / Large 18\")": ["The TIE DYE (pesto, vodka, marinara swirl) , signature",
                "Vodka", "Classic", "Fresca", "Rubirosa Supreme", "Bianca", "Sausage & Broccoli Rabe",
                "Arugula", "Honey Pie"],
            "Handmade Pasta": ["Lumache alla Vodka", "Rubirosa Ragu", "Ricotta Ravioli",
                "Short Rib Agnolotti", "Linguine alla Vongole", "Cacio e Pepe", "Carbonara", "Sunday Sauce"],
            "Antipasti": ["Garlic Knots", "Eggplant Parmigiano", "Roasted Octopus", "Baked Clams",
                "Meatballs", "Crispy Calamari"],
            "Mains": ["Chicken Al Limone", "Chicken Parmigiano"],
            "Desserts": ["Nonna's Cannoli", "Tiramisu", "Zeppole", "Rubirosa Tartufo"],
        },
    },
    "paul-jimmys": {
        "name": "Paul & Jimmy's", "type": "restaurant", "cuisine": "Southern Italian / Italian-American",
        "neighborhood": "Gramercy, Manhattan", "address": "123 East 18th St, New York, NY 10003",
        "phone": "(212) 475-9540",
        "known_for": "one of NYC's longest-running Italian restaurants (est. 1950), honest soulful cooking, "
                     "handmade pastas, and a feels-like-home atmosphere",
        "owner_phone": "",
        "reservation_policy": "We recommend reservations, and walk-ins are welcome based on availability. "
                              "We also do private events and catering.",
        "takeout": "Yes, takeout is available.",
        "hours": {"Monday": "12:00 PM - 9:00 PM", "Tuesday": "12:00 PM - 10:00 PM",
                  "Wednesday": "12:00 PM - 10:00 PM", "Thursday": "12:00 PM - 10:00 PM",
                  "Friday": "12:00 PM - 10:00 PM", "Saturday": "12:00 PM - 10:00 PM",
                  "Sunday": "12:00 PM - 9:00 PM"},
        "menu": {
            "Handmade Pasta": ["Carbonara", "Gnocchi", "Spaghetti & Meatballs", "Fettuccine Alfredo",
                "Linguine alle Vongole", "Rigatoni alla Vodka"],
            "Antipasti": ["Fried Calamari", "Burrata", "Clams Oreganata", "Eggplant Rollatini"],
            "Mains": ["Salmon with Mustard Cream Sauce", "Chicken Parmigiano", "Veal Marsala",
                "Eggplant Parmigiano"],
            "Desserts": ["Cannoli", "Tiramisu"],
        },
    },
    "carbone": {
        "name": "Carbone", "type": "restaurant", "cuisine": "Italian-American",
        "descriptor": "Michelin-starred Italian-American institution",
        "neighborhood": "Greenwich Village, Manhattan",
        "address": "181 Thompson St (between Bleecker & Houston), New York, NY 10012",
        "known_for": "Major Food Group's flagship , tuxedoed captains, tableside Caesar alla ZZ, "
                     "the Spicy Rigatoni Vodka, mid-century room with Julian Schnabel artwork; one "
                     "of the hardest tables in New York",
        "owner_phone": "",
        "reservation_policy": "Reservations are through Resy (they're released about 30 days out and "
                              "go quickly) or by email to reservations@carbonenewyork.com , we can't "
                              "honor bookings made through unauthorized third parties. Dress code: no "
                              "shorts, tank tops, or open-toed shoes. Private events are available on "
                              "request.",
        "takeout": "Ask and the team will confirm current takeout options , the dining room is the "
                   "experience.",
        "hours": {"Monday": "Dinner 5:00 PM - 11:30 PM",
                  "Tuesday": "Lunch 11:30 AM - 2:00 PM · Dinner 5:00 PM - 11:30 PM",
                  "Wednesday": "Lunch 11:30 AM - 2:00 PM · Dinner 5:00 PM - 11:30 PM",
                  "Thursday": "Lunch 11:30 AM - 2:00 PM · Dinner 5:00 PM - 11:30 PM",
                  "Friday": "Lunch 11:30 AM - 2:00 PM · Dinner 5:00 PM - 11:30 PM",
                  "Saturday": "Lunch 11:30 AM - 2:00 PM · Dinner 5:00 PM - 11:30 PM",
                  "Sunday": "Lunch 11:30 AM - 2:00 PM · Dinner 5:00 PM - 11:30 PM"},
        "menu": {
            "Signatures (the classics , team confirms the full current menu)": [
                "Spicy Rigatoni Vodka", "Veal Parmesan (for the table)", "Caesar alla ZZ (tableside)",
                "Meatballs", "Garlic Bread", "Tortellini al Ragu", "Lobster Fra Diavolo",
                "Linguine Vongole"],
            "Desserts": ["Tiramisu", "Carrot Cake", "Lemon Cheesecake"],
            "Wine & BYOB": ["Deep Italian-leaning list , sommelier guides pairings",
                "Corkage $110 per 750ml ($190 magnum); 3+ bottles coordinated in advance"],
        },
        "resy": {"venue_id": "6194", "city": "ny", "url_slug": "carbone"},
    },
    "mamo": {
        "name": "MAMO", "type": "restaurant", "cuisine": "Italian",
        "descriptor": "SoHo Italian restaurant with French Riviera roots",
        "neighborhood": "SoHo, Manhattan",
        "address": "323 W Broadway, New York, NY 10013",
        "phone": "(646) 964-4641",
        "known_for": "a modern twist on Italian nostalgia born in SoHo, sister to the Riviera "
                     "original in Antibes , famous for its truffle signatures (focaccia al tartufo, "
                     "raviolini al tartufo) and a polished, celebratory room",
        "owner_phone": "",
        "reservation_policy": "Reservations are through Resy. For private events and large parties, "
                              "email info@mamonyc.com and the team will take care of you.",
        "takeout": "Ask and the team will confirm current takeout options.",
        "hours": {"Monday": "Lunch & dinner , the team confirms exact hours",
                  "Tuesday": "Lunch & dinner , the team confirms exact hours",
                  "Wednesday": "Lunch & dinner , the team confirms exact hours",
                  "Thursday": "Lunch & dinner , the team confirms exact hours",
                  "Friday": "Lunch & dinner , the team confirms exact hours",
                  "Saturday": "Lunch & dinner , the team confirms exact hours",
                  "Sunday": "Lunch & dinner , the team confirms exact hours"},
        "menu": {
            "Antipasti": ["Carciofi alla Giudia (fried artichokes) $22", "Melanzane alla parmigiana $24",
                "Insalata di polpo e ceci (octopus, chickpeas) $25", "Tonno scottato (seared tuna) $24",
                "Burrata, barbabietole, avocado, tartufo $34", "Gamberi rossi, burrata, mango $32",
                "Carpaccio di manzo $29", "Julius Salad $16"],
            "Pizze": ["Margherita $26", "Focaccia al tartufo (fresh truffle) $70"],
            "Primi": ["La nostra Cacio & Pepe $28", "Rigatoni all'Amatriciana $32",
                "Capelli d'angelo, gamberi e pistacchi $38", "Maltagliati verdi alla bolognese $34",
                "Raviolini al tartufo (fresh truffle) $70", "Risotto al tartufo (dinner) $70",
                "Gnocchi croccanti, porri, gorgonzola (dinner) $28"],
            "Secondi": ["Pesce del giorno (market price)", "Halibut al cartoccio alla puttanesca $48",
                "Branzino, seasonal vegetables $39", "Polletto arrosto $36",
                "Tagliata di manzo (12oz NY strip) $58", "Vitello alla milanese $65"],
            "Contorni": ["Patatine fritte con tartufo e parmigiano $24", "Cavoletti $18",
                "Broccoli al forno $14", "Melanzane $18"],
            "Dolci": ["Tiramisu", "Affogato alle fragole", "My Son's Sundae",
                "Crema bruliata, frutti di bosco", "Budino al cioccolato",
                "Millefoglie al pistacchio e lamponi"],
        },
        "resy": {"venue_id": "50930", "city": "ny", "url_slug": "mamo-restaurant"},
    },
    "saint-urban": {
        "name": "Saint Urban", "type": "restaurant", "cuisine": "wine-focused European",
        "descriptor": "wine-driven European restaurant",
        "neighborhood": "Flatiron, Manhattan", "address": "43 E 20th St, New York, NY 10003",
        "phone": "(646) 988-1544",
        "known_for": "a 3,000-bottle wine cellar and a monthly-changing seasonal menu from "
                     "chef-sommelier Jared Ian Stafford-Hill (Gramercy Tavern, Veritas); named one of "
                     "The Infatuation's Best New Restaurants 2025",
        "owner_phone": "",
        "reservation_policy": "We're closed for the summer and reopening in August. We're happy to "
                              "take your name and note a reservation request for reopening , the team "
                              "will confirm dates once we're back. In season, reservations run "
                              "through Resy.",
        "takeout": "Not while we're closed for the summer , back in August.",
        "hours": {"Monday": "Closed for summer (reopening August)",
                  "Tuesday": "Closed for summer (reopening August)",
                  "Wednesday": "Closed for summer (reopening August)",
                  "Thursday": "Closed for summer (reopening August)",
                  "Friday": "Closed for summer (reopening August)",
                  "Saturday": "Closed for summer (reopening August)",
                  "Sunday": "Closed for summer (reopening August)"},
        "menu": {
            "Wine": ["A 3,000-bottle cellar , the sommelier guides pairings; ask and we'll have "
                     "the team recommend"],
            "Kitchen": ["The menu changes monthly with the season , the team confirms current "
                        "dishes when we reopen in August"],
        },
        "resy": {"venue_id": "6150", "city": "ny", "url_slug": "saint-urban-wine-bar"},
    },
    "lenlen": {
        "name": "LenLen", "type": "restaurant", "cuisine": "Thai",
        "descriptor": "Thai restaurant and late-night bar, styled after a 1970s Bangkok shophouse",
        "neighborhood": "Flatiron, Manhattan", "address": "40 E 20th St, New York, NY 10003",
        "phone": "(646) 429-8220",
        "known_for": "seasonal Bangkok-shophouse cooking from chef Peter Ki Suk Tondreau, a "
                     "design-forward retro room with a Thai grocery front, cocktails, and a "
                     "late-night menu",
        "owner_phone": "",
        "reservation_policy": "Reservations through Resy, and walk-ins are welcome. We also run a "
                              "late-night menu for the bar crowd.",
        "takeout": "We have a Thai grocery counter up front; ask and the team will confirm takeout "
                   "for kitchen dishes.",
        "hours": {"Monday": "11:30 AM - 12:00 AM", "Tuesday": "11:30 AM - 12:00 AM",
                  "Wednesday": "11:30 AM - 12:00 AM", "Thursday": "11:30 AM - 12:00 AM",
                  "Friday": "11:30 AM - 12:00 AM", "Saturday": "11:30 AM - 12:00 AM",
                  "Sunday": "11:30 AM - 12:00 AM"},
        "menu": {
            "Kitchen (seasonal , changes often, team confirms current menu)": [
                "Crab Durian Curry", "Duck Red Curry", "Chicken Green Curry", "Beef Tartare Larb",
                "Som Tum", "Wagyu Beef Boat Noodle Soup"],
            "Cocktails & Drinks": ["Five-Spice Boulevardier", "Lemongrass Gimlet",
                "Tomato Som Tum", "Thai Tea (orange blossom, cardamom)"],
            "Late Night": ["A dedicated late-night menu runs at the bar , team confirms tonight's"],
        },
        "resy": {"venue_id": "89121", "city": "ny", "url_slug": "lenlen"},
    },
    "giorgios": {
        "name": "Giorgio's of Gramercy", "type": "restaurant", "cuisine": "Italian-American",
        "neighborhood": "Gramercy, Manhattan", "address": "27 East 21st St, New York, NY 10010",
        "phone": "(212) 477-0007",
        "known_for": "warm hospitality, handmade pasta, and excellent seafood (est. 1992), an eclectic "
                     "Italian-American menu",
        "owner_phone": "",
        "resy": {"venue_id": "40209", "city": "ny", "url_slug": "giorgios-of-gramercy"},
        "reservation_policy": "Reservations are recommended and we also take walk-ins based on availability. "
                              "We offer private dining and catering.",
        "takeout": "Yes, online ordering and takeout are available.",
        "hours": {"Monday": "12:00 PM - 10:00 PM", "Tuesday": "12:00 PM - 10:00 PM",
                  "Wednesday": "12:00 PM - 10:00 PM", "Thursday": "12:00 PM - 11:00 PM",
                  "Friday": "12:00 PM - 11:00 PM", "Saturday": "5:00 PM - 11:00 PM",
                  "Sunday": "5:00 PM - 10:00 PM"},
        "menu": {
            "Starters": ["Oysters", "Burrata", "Seared Tuna Nicoise", "Grilled Octopus"],
            "Handmade Pasta": ["Seafood Pasta", "Rigatoni Bolognese", "Cacio e Pepe", "Lobster Ravioli"],
            "Mains": ["Braised Lamb Shank", "Herb-Crusted Rack of Lamb", "Branzino", "Chicken Scarpariello"],
            "Desserts": ["Tiramisu", "Panna Cotta", "Flourless Chocolate Cake"],
        },
    },
}

_ACTIVE_FILE = os.path.join(os.path.dirname(__file__), "active_business.txt")

_TENANT_TTL = 60
_tenant_cache: dict = {}   # slug -> (cfg|None, fetched_at)
_slugs_cache = (None, 0.0)
_tlock = threading.Lock()


def _fs_tenant(slug: str):
    """Firestore tenants/{slug}, cached briefly. None when absent or no Firestore."""
    now = time.time()
    with _tlock:
        hit = _tenant_cache.get(slug)
        if hit and now - hit[1] < _TENANT_TTL:
            return hit[0]
    cfg = None
    try:
        import store
        db = store.db()
        if db is not None:
            doc = db.collection("tenants").document(slug).get()
            cfg = doc.to_dict() if doc.exists else None
    except Exception:
        logger.debug("tenant fetch failed for %s", slug, exc_info=True)
    with _tlock:
        _tenant_cache[slug] = (cfg, now)
    return cfg


def invalidate(slug: str = "") -> None:
    global _slugs_cache
    with _tlock:
        (_tenant_cache.pop(slug, None) if slug else _tenant_cache.clear())
        _slugs_cache = (None, 0.0)


def known(slug: str) -> bool:
    return slug in BUSINESSES or _fs_tenant(slug) is not None


def all_slugs() -> list:
    """Seed slugs + provisioned Firestore tenants (cached)."""
    global _slugs_cache
    now = time.time()
    if _slugs_cache[0] is not None and now - _slugs_cache[1] < _TENANT_TTL:
        return _slugs_cache[0]
    slugs = list(BUSINESSES)
    try:
        import store
        db = store.db()
        if db is not None:
            for d in db.collection("tenants").limit(200).stream():
                if d.id not in slugs:
                    slugs.append(d.id)
    except Exception:
        pass
    _slugs_cache = (slugs, now)
    return slugs


def active_slug() -> str:
    """Which tenant the shared demo line currently serves. Read live , switching needs no restart."""
    try:
        with open(_ACTIVE_FILE) as f:
            s = f.read().strip()
            if s and known(s):
                return s
    except OSError:
        pass
    return os.getenv("ACTIVE_BUSINESS", "rubirosa")


def get(slug: str) -> dict:
    return _fs_tenant(slug) or BUSINESSES.get(slug) or BUSINESSES["rubirosa"]


def menu_text(cfg: dict) -> str:
    out = []
    for section, items in cfg["menu"].items():
        out.append(f"{section}:")
        out.extend(f"  - {i}" for i in items)
    return "\n".join(out)


def hours_text(cfg: dict) -> str:
    return "\n".join(f"  {d}: {h}" for d, h in cfg["hours"].items())
