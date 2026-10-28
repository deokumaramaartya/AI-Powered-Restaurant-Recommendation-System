"""Small hand-built dataset shared by the pytest suite and offline evals.

Thresholds are 500 / 1200 so the tier boundaries (500, 501, 1200, 1201) can be exercised.
"""

from __future__ import annotations

import pandas as pd

FIXTURE_LOW_MAX = 500
FIXTURE_MEDIUM_MAX = 1200


def _row(
    id,
    name,
    location,
    cuisines,
    rating,
    votes,
    cost,
    rest_type="Casual Dining",
    dishes=(),
    listing_types=("Dine-out",),
    online_order=True,
    book_table=False,
):
    return {
        "id": id,
        "name": name,
        "location": location,
        "area": location,
        "address": f"{name} Road, {location}, Bangalore",
        "cuisines": list(cuisines),
        "rating": float(rating),
        "votes": int(votes),
        "cost_for_two": int(cost),
        "rest_type": rest_type,
        "listing_types": list(listing_types),
        "online_order": online_order,
        "book_table": book_table,
        "dish_liked": list(dishes),
    }


ROWS = [
    # Indiranagar: exactly five medium Italian places rated 4.0 or above.
    _row("ind-1", "Toscano", "Indiranagar", ["Italian", "Continental"], 4.5, 2100, 1100,
         dishes=["Pasta", "Risotto", "Tiramisu", "Pizza", "Lasagne", "Bruschetta"], book_table=True),
    _row("ind-2", "Pasta Street", "Indiranagar", ["Italian"], 4.3, 900, 800, dishes=["Pasta"]),
    _row("ind-3", "Little Italy", "Indiranagar", ["Italian", "Pizza"], 4.1, 1500, 1000),
    _row("ind-4", "Fio Cookhouse", "Indiranagar", ["Italian", "European"], 4.0, 300, 1200, rest_type="Cafe"),
    _row("ind-5", "Smoke House Deli", "Indiranagar", ["Italian", "American"], 4.4, 50, 700, rest_type="Cafe"),
    _row("ind-6", "Chianti", "Indiranagar", ["Italian"], 3.8, 400, 900),
    _row("ind-7", "Toit", "Indiranagar", ["Continental", "Italian", "Finger Food", "Pizza"], 4.6, 5000, 1500,
         rest_type="Microbrewery"),
    _row("ind-8", "Burma Burma", "Indiranagar", ["Burmese"], 4.7, 3000, 1300, rest_type=None),
    # Koramangala 5th Block: five low-budget North Indian places rated 3.5 or above.
    _row("kor-1", "Punjabi Tadka", "Koramangala 5th Block", ["North Indian"], 4.0, 800, 300, rest_type="Quick Bites"),
    _row("kor-2", "Delhi Highway", "Koramangala 5th Block", ["North Indian", "Mughlai"], 3.9, 500, 400),
    _row("kor-3", "Paratha Plaza", "Koramangala 5th Block", ["North Indian"], 3.7, 120, 250, rest_type="Quick Bites"),
    _row("kor-4", "Truffles", "Koramangala 5th Block", ["American", "Burger"], 4.5, 9000, 900),
    _row("kor-5", "Dhaba Express", "Koramangala 5th Block", ["North Indian", "Chinese"], 3.6, 60, 450),
    _row("kor-6", "Roti Ghar", "Koramangala 5th Block", ["North Indian"], 3.5, 0, 200, rest_type="Quick Bites"),
    # BTM: a low-budget Mexican request at 4.8 needs every relaxation step.
    _row("btm-1", "Taco Bell", "BTM", ["Mexican"], 4.0, 700, 400, rest_type="Quick Bites"),
    _row("btm-2", "Chinese Wok", "BTM", ["Chinese"], 4.1, 300, 350),
    _row("btm-3", "Biryani House", "BTM", ["Biryani", "North Indian"], 4.4, 1200, 600),
    _row("btm-4", "Cafe Coffee Day", "BTM", ["Cafe"], 3.9, 200, 450, rest_type="Cafe"),
    _row("btm-5", "Meghana Foods", "BTM", ["Andhra", "Biryani"], 4.5, 4000, 700),
    _row("btm-6", "Empire", "BTM", ["North Indian"], 3.6, 900, 500),
    # Whitefield: one Japanese place rated 3.1, so narrow medium Japanese requests relax.
    _row("wf-1", "Sushi Haus", "Whitefield", ["Japanese"], 3.1, 40, 1000),
    _row("wf-2", "Whitefield Diner", "Whitefield", ["Continental", "American"], 3.4, 150, 800),
    _row("wf-3", "Spice Route", "Whitefield", ["North Indian"], 4.2, 600, 550),
    _row("wf-4", "Idli Corner", "Whitefield", ["South Indian"], 3.8, 300, 150, rest_type="Quick Bites"),
    # Jayanagar: costs sit exactly on the 500 / 1200 tier boundaries.
    _row("jay-1", "Boundary Five Hundred", "Jayanagar", ["South Indian"], 4.0, 100, 500),
    _row("jay-2", "Boundary Five Oh One", "Jayanagar", ["South Indian"], 4.0, 100, 501),
    _row("jay-3", "Boundary Twelve Hundred", "Jayanagar", ["South Indian"], 4.0, 100, 1200),
    _row("jay-4", "Boundary Twelve Oh One", "Jayanagar", ["South Indian"], 4.0, 100, 1201),
    # Old Airport Road: nothing rated 3.0 or above, so every request is empty.
    _row("oar-1", "Lonely Diner", "Old Airport Road", ["Chinese"], 2.6, 20, 400),
]


def fixture_frame() -> pd.DataFrame:
    return pd.DataFrame([dict(row) for row in ROWS])
