#!/usr/bin/env python3
"""Generate a reproducible synthetic transaction file with injected anomalies.

Writes data/sample_transactions.csv and data/sample_labels.csv (ground truth).
The data is entirely synthetic.
"""

import argparse
import csv
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

MERCHANTS = [
    "Maple Grocer", "City Transit", "Corner Cafe", "Northside Pharmacy", "Bluebird Books",
    "Harbour Fuel", "Metro Market", "Sunrise Bakery", "Peak Fitness", "Urban Threads",
    "Lakeview Diner", "TechMart", "Green Leaf Organics", "Quick Print", "Riverside Cinema",
    "Cozy Home Goods", "Prime Pet Supply", "Orbit Mobile", "Willow Salon", "Delta Hardware",
]
HOME_COUNTRIES = ["CA"] * 14 + ["US"] * 6 + ["GB"] * 3
FAR_COUNTRIES = ["JP", "BR", "AU", "ZA", "NG"]
CHANNELS = ["pos", "online", "online", "pos", "atm"]


def fmt(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def generate(seed: int = 42, accounts: int = 23, days: int = 30):
    rng = random.Random(seed)
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    rows, labels = [], []
    counter = 0

    def add(account, ts, amount, merchant, country, channel="online", anomaly=None):
        nonlocal counter
        counter += 1
        tid = f"T{counter:06d}"
        rows.append({
            "transaction_id": tid, "account_id": account, "timestamp": fmt(ts),
            "amount": f"{amount:.2f}", "merchant": merchant, "country": country,
            "channel": channel, "category": "retail",
        })
        if anomaly:
            labels.append({"transaction_id": tid, "anomaly_type": anomaly})
        return tid

    profiles = {}
    for n in range(accounts):
        acct = f"ACC{n + 1:03d}"
        profiles[acct] = {
            "country": HOME_COUNTRIES[n % len(HOME_COUNTRIES)],
            "median": rng.uniform(25, 90),
            "sigma": rng.uniform(0.35, 0.6),
            "favourites": rng.sample(MERCHANTS, 6),
            "count": rng.randint(70, 110),
        }

    # Normal behaviour: daytime spending around each account's own typical amount.
    for acct, p in profiles.items():
        for _ in range(p["count"]):
            day = rng.randrange(days)
            hour = rng.choices(range(24), weights=[0, 0, 0, 0, 0, 1, 2, 4, 6, 6, 6, 8, 9, 7, 6, 6, 7, 8, 8, 6, 4, 2, 1, 0])[0]
            ts = start + timedelta(days=day, hours=hour, minutes=rng.randrange(60), seconds=rng.randrange(60))
            amount = min(max(rng.lognormvariate(math.log(p["median"]), p["sigma"]), 2.0), 450.0)
            add(acct, ts, amount, rng.choice(p["favourites"]), p["country"], rng.choice(CHANNELS))

    # Injected anomalies (late in the month so every account has history first).
    accts = list(profiles)
    rng.shuffle(accts)
    pool = iter(accts * 4)

    def pick():
        acct = next(pool)
        return acct, profiles[acct]

    for _ in range(6):  # very large purchase
        acct, p = pick()
        ts = start + timedelta(days=rng.randrange(20, 29), hours=rng.randrange(10, 20), minutes=rng.randrange(60))
        add(acct, ts, rng.uniform(8000, 15000), rng.choice(p["favourites"]), p["country"], "online", "large_amount")

    for _ in range(4):  # burst of rapid small purchases
        acct, p = pick()
        base = start + timedelta(days=rng.randrange(20, 29), hours=rng.randrange(10, 20), minutes=rng.randrange(60))
        for i in range(7):
            add(acct, base + timedelta(seconds=45 * i), rng.uniform(30, 120), rng.choice(p["favourites"]),
                p["country"], "online", "velocity_burst")

    for _ in range(4):  # impossible travel
        acct, p = pick()
        base = start + timedelta(days=rng.randrange(20, 29), hours=rng.randrange(10, 18), minutes=rng.randrange(60))
        far = rng.choice(FAR_COUNTRIES)
        add(acct, base, rng.uniform(20, 90), rng.choice(p["favourites"]), p["country"], "pos")
        add(acct, base + timedelta(minutes=20), rng.uniform(900, 1800), "Global Duty Free", far, "pos", "geo_jump")

    for _ in range(4):  # night-time purchase abroad
        acct, p = pick()
        ts = start + timedelta(days=rng.randrange(20, 29), hours=2, minutes=rng.randrange(60))
        add(acct, ts, rng.uniform(1500, 3500), "Night Electronics", rng.choice(FAR_COUNTRIES), "online", "night_foreign")

    for _ in range(4):  # same charge twice
        acct, p = pick()
        base = start + timedelta(days=rng.randrange(20, 29), hours=rng.randrange(10, 20), minutes=rng.randrange(60))
        merchant, amount = rng.choice(p["favourites"]), round(rng.uniform(150, 400), 2)
        add(acct, base, amount, merchant, p["country"], "pos")
        add(acct, base + timedelta(seconds=30), amount, merchant, p["country"], "pos", "duplicate_charge")

    for _ in range(5):  # unusually large for this account, but under the fixed limits
        acct, p = pick()
        ts = start + timedelta(days=rng.randrange(20, 29), hours=rng.randrange(10, 20), minutes=rng.randrange(60))
        add(acct, ts, p["median"] * rng.uniform(14, 22), rng.choice(p["favourites"]), p["country"], "online", "amount_outlier")

    rows.sort(key=lambda r: (r["timestamp"], r["transaction_id"]))
    return rows, labels


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out-dir", default="data")
    args = parser.parse_args()

    rows, labels = generate(seed=args.seed)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "sample_transactions.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with open(out / "sample_labels.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["transaction_id", "anomaly_type"])
        writer.writeheader()
        writer.writerows(labels)
    print(f"Wrote {len(rows)} transactions ({len(labels)} labelled anomalies) to {out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
