"""Model calls. Reuses bench.py's runners for real models; adds deterministic mock models for tests and demos.

Mock models never appear in results you publish: they exist so the harness and graders can be tested without API spend.
  mock:good   answers correctly from the truth record, in the structure the prompt asks for
  mock:lazy   names the wrong campaign first and ignores anomalies and blanks
"""
import re
import time

import bench


def call(model, prompt, case=None, prompt_meta=None):
    if model.startswith("mock:"):
        return _mock(model.split(":", 1)[1], case, prompt_meta or {})
    t0 = time.time()
    r = bench.run_model(model, prompt)
    if r.get("error"):
        return {"error": r["error"]}
    r["latency_ms"] = int(round(r.get("latency", time.time() - t0) * 1000))
    return r


def _mock(kind, case, meta):
    t = case["truth"]
    names = t["ranked_by_cpa"] or list(t["campaigns"])
    order = names if kind == "good" else list(reversed(names))
    lines = []
    sections = meta.get("sections") or []
    if sections:
        lines.append(f"{sections[0]}\n" + f"{order[0]} is the most efficient campaign.")
        lines.append(f"{sections[1] if len(sections) > 1 else 'RANKING'}")
        for n in order:
            a = t["campaigns"][n]
            cpa = f"${a['cpa']:.2f} per conversion" if a["cpa"] is not None else "no conversions"
            lines.append(f"{n}: {cpa}.")
        lines.append(sections[2] if len(sections) > 2 else "MOVE")
        lines.append("Shift budget toward the leader.")
        lines.append(sections[3] if len(sections) > 3 else "WATCH")
    else:
        lines.append(f"Best ROI: {order[0]}.")
        for n in order:
            a = t["campaigns"][n]
            if a["cpa"] is not None:
                lines.append(f"{n} costs ${a['cpa']:.2f} per conversion.")
    if kind == "good":
        if t.get("anomaly"):
            y, m, d = t["anomaly"]["date"].split("-")
            lines.append(f"Watch {t['anomaly']['campaign']} on {['', 'Jan', 'Feb', 'Mar'][int(m)]} {int(d)}: {t['anomaly']['kind']}.")
        for z in t.get("zero_conversion_campaigns", []):
            lines.append(f"{z} spent budget with no conversions.")
        if t.get("missing_values"):
            lines.append("Some conversion cells are blank, so totals may be understated.")
    return {"output": "\n".join(lines), "latency_ms": 5, "tokens_in": 800, "tokens_out": 150, "cost": 0.0}
