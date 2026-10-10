"""Seeded golden set: synthetic marketing datasets whose right answers are computed, not guessed.

Each case is a CSV plus a truth record (best/worst campaign by cost per conversion, totals, injected anomaly,
zero-conversion campaigns, missing values). Regenerating with the same seed gives byte-identical files.
"""
import csv
import json
import os
import random
from datetime import date, timedelta

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN_DIR = os.path.join(HERE, "golden")
FIELDS = ["date", "account_name", "campaign_name", "channel", "impressions", "clicks", "conversions", "cost"]

# name, channel, base daily cost, clicks per $, conversion rate on clicks
CAMPAIGNS = [
    ("Spring Sale - Search", "Google Ads", 1100, 0.72, 0.050),
    ("Spring Sale - Social", "Facebook Ads", 1000, 0.85, 0.040),
    ("Lawn Care Promo", "Google Ads", 900, 0.74, 0.056),
    ("Brand Awareness - Video", "YouTube Ads", 800, 0.60, 0.022),
    ("Retargeting - Display", "Display", 500, 0.95, 0.075),
    ("Newsletter Signup", "LinkedIn Ads", 650, 0.55, 0.034),
]


def _rows(rng, n_campaigns, days, scale=1.0, start=date(2026, 3, 1)):
    chosen = CAMPAIGNS[:n_campaigns]
    out = []
    for d in range(days):
        day = start + timedelta(days=d)
        for name, channel, base, cpc_rate, cr in chosen:
            cost = round(base * scale * rng.uniform(0.85, 1.15))
            clicks = max(1, round(cost * cpc_rate * rng.uniform(0.9, 1.1)))
            conv = max(0, round(clicks * cr * rng.uniform(0.8, 1.2)))
            impr = round(clicks * rng.uniform(14, 22))
            out.append({"date": day.isoformat(), "account_name": "Demo Account", "campaign_name": name, "channel": channel,
                        "impressions": impr, "clicks": clicks, "conversions": conv, "cost": cost})
    return out


def _case_specs():
    """(case_id, description, builder). Builders mutate rows and return injected-fact dict."""
    def plain(rng):
        return _rows(rng, 4, 14), {}

    def spike(rng):
        rows = _rows(rng, 4, 14)
        r = [x for x in rows if x["campaign_name"] == "Spring Sale - Social" and x["date"] == "2026-03-09"][0]
        r["cost"] = r["cost"] * 4
        return rows, {"anomaly": {"campaign": "Spring Sale - Social", "date": "2026-03-09", "kind": "cost spike, conversions flat"}}

    def zero_conv(rng):
        rows = _rows(rng, 4, 14)
        for x in rows:
            if x["campaign_name"] == "Brand Awareness - Video":
                x["conversions"] = 0
        return rows, {"zero_conversion_campaigns": ["Brand Awareness - Video"]}

    def single(rng):
        return _rows(rng, 1, 14), {}

    def close(rng):
        rows = _rows(rng, 3, 14)
        for x in rows:
            if x["campaign_name"] == "Spring Sale - Social":
                x["conversions"] = round(x["conversions"] * 1.28)
        return rows, {}

    def missing(rng):
        rows = _rows(rng, 4, 14)
        for i in (5, 17, 33):
            rows[i]["conversions"] = ""
        return rows, {"missing_values": True}

    def big(rng):
        return _rows(rng, 5, 14, scale=60), {}

    def drop(rng):
        rows = _rows(rng, 4, 14)
        for x in rows:
            if x["campaign_name"] == "Lawn Care Promo" and x["date"] >= "2026-03-10":
                x["conversions"] = round(x["conversions"] * 0.2)
        return rows, {"anomaly": {"campaign": "Lawn Care Promo", "date": "2026-03-10", "kind": "conversions collapse from this date"}}

    def many(rng):
        return _rows(rng, 6, 14), {}

    def short(rng):
        return _rows(rng, 3, 3), {}

    def spike_zero(rng):
        rows = _rows(rng, 5, 14)
        r = [x for x in rows if x["campaign_name"] == "Retargeting - Display" and x["date"] == "2026-03-04"][0]
        r["cost"] = r["cost"] * 5
        for x in rows:
            if x["campaign_name"] == "Brand Awareness - Video":
                x["conversions"] = 0
        return rows, {"anomaly": {"campaign": "Retargeting - Display", "date": "2026-03-04", "kind": "cost spike, conversions flat"},
                      "zero_conversion_campaigns": ["Brand Awareness - Video"]}

    def retarget_best(rng):
        return _rows(rng, 5, 21), {}

    return [
        ("c01_plain", "Four campaigns, no tricks", plain),
        ("c02_spike", "One-day cost spike on one campaign", spike),
        ("c03_zero_conv", "A campaign with spend and zero conversions", zero_conv),
        ("c04_single", "One campaign only", single),
        ("c05_close", "Two campaigns with close cost per conversion", close),
        ("c06_missing", "Blank conversion cells", missing),
        ("c07_big", "Large numbers", big),
        ("c08_drop", "Conversions collapse from a date", drop),
        ("c09_many", "Six campaigns", many),
        ("c10_short", "Only three days of data", short),
        ("c11_spike_zero", "Spike plus zero-conversion campaign", spike_zero),
        ("c12_retarget", "Five campaigns over three weeks", retarget_best),
    ]


def compute_truth(rows, injected):
    agg = {}
    for r in rows:
        a = agg.setdefault(r["campaign_name"], {"channel": r["channel"], "cost": 0.0, "conversions": 0, "clicks": 0})
        a["cost"] += float(r["cost"])
        a["clicks"] += int(r["clicks"])
        if r["conversions"] not in ("", None):
            a["conversions"] += int(r["conversions"])
    for a in agg.values():
        a["cpa"] = round(a["cost"] / a["conversions"], 2) if a["conversions"] else None
    ranked = sorted([k for k, a in agg.items() if a["cpa"] is not None], key=lambda k: agg[k]["cpa"])
    truth = {
        "campaigns": agg,
        "ranked_by_cpa": ranked,
        "best": ranked[0] if ranked else None,
        "worst": ranked[-1] if len(ranked) > 1 else None,
        "total_cost": round(sum(a["cost"] for a in agg.values()), 2),
        "total_conversions": sum(a["conversions"] for a in agg.values()),
        "dates": sorted({r["date"] for r in rows}),
    }
    truth.update(injected)
    return truth


def generate(seed=7):
    os.makedirs(os.path.join(GOLDEN_DIR, "data"), exist_ok=True)
    truth_all, index = {}, []
    for i, (cid, desc, builder) in enumerate(_case_specs()):
        rng = random.Random(seed * 1000 + i)
        rows, injected = builder(rng)
        path = os.path.join(GOLDEN_DIR, "data", cid + ".csv")
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
        truth_all[cid] = compute_truth(rows, injected)
        index.append({"case_id": cid, "description": desc, "file": "golden/data/" + cid + ".csv"})
    with open(os.path.join(GOLDEN_DIR, "truth.json"), "w") as f:
        json.dump({"seed": seed, "cases": index, "truth": truth_all}, f, indent=2, sort_keys=True)
    return len(index)


def load():
    with open(os.path.join(GOLDEN_DIR, "truth.json")) as f:
        g = json.load(f)
    cases = []
    for c in g["cases"]:
        with open(os.path.join(HERE, c["file"])) as f:
            data = f.read()
        cases.append({**c, "csv": data, "truth": g["truth"][c["case_id"]]})
    return cases
