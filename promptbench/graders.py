"""Deterministic graders. Every check is computed from the case's ground truth, no LLM involved.

Checks (a check is "n/a" when the case does not exercise it):
  best_campaign      the campaign the answer leads with is the best by cost per conversion
  anomaly_flagged    an injected anomaly is named (campaign and date)
  zero_conv_flagged  a campaign with spend and no conversions is called out as such
  missing_flagged    blank cells in the data are acknowledged
  cpa_correct        every cost-per-conversion figure next to a campaign name is within 2% of the truth
  sections           the prompt's required section headings all appear, in order (from prompts/<name>.meta.json)
  cpa_every_entry    (v6-style rule) every ranked campaign has a cost-per-conversion figure on its line
A prompt's meta.json may list checks to skip (for example a prompt that does not ask which campaign is best).
The checks measure outcomes of the shared task (a correct, grounded performance brief), not each prompt's own wording.
Also reports `ungrounded`: numbers of 50 or more that match nothing in the data or its aggregates (informational,
recommendations legitimately introduce new figures, so this is not part of pass/fail).
"""
import re
from datetime import date

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
NUM = re.compile(r"\$?\s*([0-9][0-9,]*\.?[0-9]*)")


def _numbers(text):
    out = []
    for m in NUM.finditer(text):
        s = m.group(1).replace(",", "")
        try:
            out.append(float(s))
        except ValueError:
            pass
    return out


def _date_variants(iso):
    y, m, d = (int(x) for x in iso.split("-"))
    name = MONTHS[m - 1]
    pats = [iso, f"{name} {d}", f"{name[:3]} {d}", f"{name} {d:02d}", f"{name[:3]} {d:02d}", f"{m}/{d}", f"{m:02d}/{d:02d}", f"{d} {name}", f"{d} {name[:3]}"]
    return [p.lower() for p in pats]


def _mentions_date(text, iso):
    t = text.lower()
    for p in _date_variants(iso):
        if re.search(r"(?<![0-9a-z])" + re.escape(p) + r"(?![0-9])", t):
            return True
    return False


def first_named_campaign(text, names):
    """The campaign whose name appears earliest in the answer (case-insensitive)."""
    t = text.lower()
    best, pos = None, None
    for n in names:
        i = t.find(n.lower())
        if i != -1 and (pos is None or i < pos):
            best, pos = n, i
    return best


def check_best(text, truth):
    if not truth.get("best"):
        return None
    if len(truth["campaigns"]) == 1:
        return None
    claimed = first_named_campaign(text, list(truth["campaigns"]))
    return claimed == truth["best"]


def check_anomaly(text, truth):
    a = truth.get("anomaly")
    if not a:
        return None
    return a["campaign"].lower() in text.lower() and _mentions_date(text, a["date"])


def check_zero_conv(text, truth):
    z = truth.get("zero_conversion_campaigns")
    if not z:
        return None
    t = text.lower()
    phrases = ("no conversion", "zero conversion", "0 conversion", "without conversion", "not a single conversion", "no conversions", "zero conv", "failed to convert", "no return")
    for name in z:
        # look in a window around each mention of the campaign
        for m in re.finditer(re.escape(name.lower()), t):
            window = t[max(0, m.start() - 160): m.end() + 200]
            if any(p in window for p in phrases):
                break
        else:
            return False
    return True


def check_missing(text, truth):
    if not truth.get("missing_values"):
        return None
    t = text.lower()
    return any(w in t for w in ("missing", "blank", "null", "incomplete", "empty", "not recorded", "gaps", "no data for", "unreported"))


def check_cpa_correct(text, truth):
    """A dollar figure labelled per conversion/CPA, in the same line and before the next campaign name, is attributed to that campaign."""
    low = text.lower()
    positions = sorted((m.start(), n) for n in truth["campaigns"] for m in re.finditer(re.escape(n.lower()), low))
    checked, bad = 0, 0
    for idx, (start, name) in enumerate(positions):
        a = truth["campaigns"][name]
        if a["cpa"] is None:
            continue
        stop = min(start + len(name) + 140, len(text))
        nl = text.find("\n", start)
        if nl != -1:
            stop = min(stop, nl)
        if idx + 1 < len(positions):
            stop = min(stop, positions[idx + 1][0])
        seg = text[start + len(name): stop]
        for pm in re.finditer(r"\$\s*([0-9][0-9,]*\.?[0-9]*)", seg):
            label = seg[pm.start(): pm.end() + 28].lower()
            if not any(k in label for k in ("per conv", "/conv", "cpa", "cost per", "per acquisition", "each")):
                continue
            val = float(pm.group(1).replace(",", ""))
            checked += 1
            if abs(val - a["cpa"]) > max(0.02 * a["cpa"], 0.51):
                bad += 1
    if checked == 0:
        return None
    return bad == 0


def check_sections(text, sections):
    if not sections:
        return None
    pos = -1
    up = text.upper()
    for s in sections:
        i = up.find(s.upper(), pos + 1)
        if i == -1:
            return False
        pos = i
    return True


def check_cpa_every_entry(text, truth, enabled):
    if not enabled:
        return None
    ranked = [k for k, a in truth["campaigns"].items() if a["cpa"] is not None]
    if not ranked:
        return None
    for name in ranked:
        i = text.lower().find(name.lower())
        if i == -1:
            return False
        window = text[i: i + 140]
        if not re.search(r"\$\s*[0-9]", window):
            return False
    return True


def allowed_numbers(truth, csv_text):
    allowed = set()
    for n in _numbers(csv_text):
        allowed.add(round(n, 2))
    for a in truth["campaigns"].values():
        for k in ("cost", "conversions", "clicks", "cpa"):
            if a.get(k) is not None:
                allowed.add(round(float(a[k]), 2))
    allowed.add(round(truth["total_cost"], 2))
    allowed.add(float(truth["total_conversions"]))
    return allowed


def ungrounded_count(text, truth, csv_text):
    allowed = allowed_numbers(truth, csv_text)
    n = 0
    for v in _numbers(text):
        if v < 50 or 2024 <= v <= 2030:
            continue
        if any(abs(v - a) <= max(0.015 * a, 0.51) for a in allowed):
            continue
        n += 1
    return n


def grade(text, case, meta=None):
    meta = meta or {}
    truth = case["truth"]
    checks = {
        "best_campaign": check_best(text, truth),
        "anomaly_flagged": check_anomaly(text, truth),
        "zero_conv_flagged": check_zero_conv(text, truth),
        "missing_flagged": check_missing(text, truth),
        "cpa_correct": check_cpa_correct(text, truth),
        "sections": check_sections(text, meta.get("sections")),
        "cpa_every_entry": check_cpa_every_entry(text, truth, meta.get("cpa_every_entry", False)),
    }
    for k in meta.get("skip", []):
        checks[k] = None
    applicable = {k: v for k, v in checks.items() if v is not None}
    passed = bool(applicable) and all(applicable.values())
    score = (sum(1 for v in applicable.values() if v) / len(applicable)) if applicable else None
    return {"checks": checks, "passed": passed, "score": score, "ungrounded": ungrounded_count(text, truth, case["csv"])}
