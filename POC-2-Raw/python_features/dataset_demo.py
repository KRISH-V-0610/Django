"""C. Python datasets (plain stdlib) - run with: uv run python -m python_features.dataset_demo"""
from collections import defaultdict

# Row-oriented: a list of dicts. Easy to read, easy to filter.
ORDERS = [
    {"id": 1, "customer": "asha", "category": "books", "amount": 250.0},
    {"id": 2, "customer": "ravi", "category": "tech", "amount": 1200.0},
    {"id": 3, "customer": "asha", "category": "tech", "amount": 800.0},
    {"id": 4, "customer": "meera", "category": "books", "amount": None},  # missing
    {"id": 5, "customer": "ravi", "category": "books", "amount": "oops"},  # invalid
    {"id": 6, "customer": "meera", "category": "tech", "amount": -50.0},  # invalid
]


def clean(rows):
    """Keep only rows with a valid positive number amount. Return (good, bad)."""
    good, bad = [], []
    for row in rows:
        amount = row.get("amount")
        if isinstance(amount, (int, float)) and amount > 0:
            good.append(row)
        else:
            bad.append(row)
    return good, bad


def filter_by_category(rows, category):
    return [r for r in rows if r["category"] == category]


def add_tax(rows, rate=0.18):  # transformation: new dicts, originals untouched
    return [{**r, "total": round(r["amount"] * (1 + rate), 2)} for r in rows]


def total_per_customer(rows):  # aggregation
    totals = defaultdict(float)
    for r in rows:
        totals[r["customer"]] += r["amount"]
    return dict(totals)


def to_columns(rows):
    """Row-oriented -> column-oriented (dict of lists). Good for column maths."""
    return {key: [r[key] for r in rows] for key in rows[0]}


def compare_structures():
    """Same data, different shapes: where each one is good."""
    by_id = {r["id"]: r for r in ORDERS}  # dict: lookup by key
    ids_list = [r["id"] for r in ORDERS]  # list: needs a scan
    ids_set = set(ids_list)               # set: fast membership, no duplicates
    return by_id[3], 3 in ids_list, 3 in ids_set


if __name__ == "__main__":
    good, bad = clean(ORDERS)
    print("valid:", len(good), "invalid:", [r["id"] for r in bad])
    print("tech only:", filter_by_category(good, "tech"))
    print("with tax:", add_tax(good)[0])
    print("per customer:", total_per_customer(good))
    print("columns:", to_columns(good)["amount"])
    print("lookups:", compare_structures())
