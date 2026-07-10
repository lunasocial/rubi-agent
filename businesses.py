"""All businesses this deployment serves, keyed by slug. The 904 line serves whichever slug is ACTIVE
(see active_business()); each business has its own dashboard at /<slug>/ and its own isolated data.
Menus/prices are seeded from public info , owner confirms.
"""
import os

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


def active_slug() -> str:
    """Which business the phone line currently serves. Read live per request , switching needs no restart."""
    try:
        with open(_ACTIVE_FILE) as f:
            s = f.read().strip()
            if s in BUSINESSES:
                return s
    except OSError:
        pass
    return os.getenv("ACTIVE_BUSINESS", "rubirosa")


def get(slug: str) -> dict:
    return BUSINESSES.get(slug) or BUSINESSES["rubirosa"]


def menu_text(cfg: dict) -> str:
    out = []
    for section, items in cfg["menu"].items():
        out.append(f"{section}:")
        out.extend(f"  - {i}" for i in items)
    return "\n".join(out)


def hours_text(cfg: dict) -> str:
    return "\n".join(f"  {d}: {h}" for d, h in cfg["hours"].items())
