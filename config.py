"""Business config for the receptionist agent. One business per deployment (v1).

Everything here is owner-editable and is what the agent is allowed to state as fact.
Prices/hours are seeded from public info and should be confirmed by the owner.
"""

BUSINESS = {
    "name": "Rubirosa",
    "type": "restaurant",
    "cuisine": "Italian-American",
    "neighborhood": "Nolita, Manhattan",
    "address": "235 Mulberry St, New York, NY 10012",
    "known_for": "thin-crust pizza (the famous TIE DYE and Vodka pies), handmade pasta, "
                 "and reinvented Italian-American classics",
    "owner_phone": "",   # set to the manager's number for escalation (e.g. +1...)
    "reservation_policy": (
        "We take reservations for parties of 1-8. Larger groups and private events are handled "
        "separately. Walk-ins are welcome but there's often a wait on weekends."
    ),
    "takeout": "Yes, pickup and delivery are available for pizza and pasta.",
    "hours": {
        "Monday": "11:30 AM - 11:00 PM",
        "Tuesday": "11:30 AM - 11:00 PM",
        "Wednesday": "11:30 AM - 11:00 PM",
        "Thursday": "11:30 AM - 11:00 PM",
        "Friday": "11:30 AM - 12:00 AM",
        "Saturday": "11:00 AM - 12:00 AM",
        "Sunday": "11:00 AM - 11:00 PM",
    },
    # Representative menu. Prices are placeholders , owner confirms. Items are real.
    "menu": {
        "Pizza (Small 14\" / Large 18\")": [
            "The TIE DYE (pesto, vodka, and marinara swirl) , the signature",
            "Vodka", "Classic", "Fresca", "Rubirosa Supreme", "Bianca",
            "Sausage & Broccoli Rabe", "Arugula", "Honey Pie",
        ],
        "Handmade Pasta": [
            "Lumache alla Vodka", "Rubirosa Ragu", "Ricotta Ravioli", "Short Rib Agnolotti",
            "Linguine alla Vongole", "Roman Cacio e Pepe", "Carbonara", "Sunday Sauce",
            "Family Size Lasagne",
        ],
        "Antipasti & Fritti": [
            "Garlic Knots", "Eggplant Parmigiano", "Grandma's Braciole", "Roasted Octopus",
            "Baked Clams", "Meatballs", "Mozzarella Sticks", "Crispy Calamari",
        ],
        "Salads": ["Heirloom Caprese", "Rubirosa Salad", "Arugula", "Caesar", "Chopped Antipasto"],
        "Mains": ["Chicken Al Limone", "Chicken Parmigiano", "Eggplant Parmigiano"],
        "Desserts": ["Nonna's Cannoli", "Tiramisu", "Zeppole", "N.Y. Cheesecake", "Rubirosa Tartufo"],
    },
}


def menu_text() -> str:
    lines = []
    for section, items in BUSINESS["menu"].items():
        lines.append(f"{section}:")
        lines.extend(f"  - {i}" for i in items)
    return "\n".join(lines)


def hours_text() -> str:
    return "\n".join(f"  {day}: {h}" for day, h in BUSINESS["hours"].items())
