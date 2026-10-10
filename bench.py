#!/usr/bin/env python3
"""
bench.py — Multi-model prompt benchmarking CLI

Usage:
  python bench.py --prompt prompts/analyze.txt --file data/sample_marketing_data.csv
  python bench.py --prompt prompts/analyze.txt --file data/sample_marketing_data.csv --models claude-sonnet-4-6,gpt-4o
  python bench.py --prompt prompts/analyze.txt --file data/sample_marketing_data.csv --models all
  python bench.py --list
  python bench.py --compare results/run1.json results/run2.json
"""

import argparse
import os
import time
import json
import sys
from datetime import datetime
from typing import Optional

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")

# ── Pricing table (USD per 1M tokens) ────────────────────────────────────────
PRICING = {
    "claude-opus-4-6":     {"input": 15.00, "output": 75.00},
    "claude-sonnet-4-6":   {"input":  3.00, "output": 15.00},
    "claude-haiku-4-5":    {"input":  0.80, "output":  4.00},
    "gpt-4o":              {"input":  2.50, "output": 10.00},
    "gpt-4o-mini":         {"input":  0.15, "output":  0.60},
    "gemini-1.5-pro":      {"input":  3.50, "output": 10.50},
    "gemini-1.5-flash":    {"input":  0.075,"output":  0.30},
}

CLAUDE_MODELS  = ["claude-sonnet-4-6", "claude-haiku-4-5", "claude-opus-4-6"]
OPENAI_MODELS  = ["gpt-4o", "gpt-4o-mini"]
GEMINI_MODELS  = ["gemini-1.5-pro", "gemini-1.5-flash"]
DEFAULT_MODELS = ["claude-sonnet-4-6", "claude-haiku-4-5"]
JUDGE_MODEL    = "claude-sonnet-4-6"

# ── Model runners ─────────────────────────────────────────────────────────────

def run_claude(model: str, prompt: str) -> dict:
    import anthropic
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return {"error": "ANTHROPIC_API_KEY not set"}
    client = anthropic.Anthropic(api_key=key)
    t0 = time.time()
    msg = client.messages.create(
        model=model,
        max_tokens=1024,
        temperature=0.2,
        messages=[{"role": "user", "content": prompt}],
    )
    latency = time.time() - t0
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    tokens_in  = msg.usage.input_tokens
    tokens_out = msg.usage.output_tokens
    price = PRICING.get(model, {"input": 0, "output": 0})
    cost  = (tokens_in * price["input"] + tokens_out * price["output"]) / 1_000_000
    return {"output": text, "latency": latency, "tokens_in": tokens_in, "tokens_out": tokens_out, "cost": cost}


def run_openai(model: str, prompt: str) -> dict:
    try:
        from openai import OpenAI
    except ImportError:
        return {"error": "openai package not installed. Run: pip install openai"}
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return {"error": "OPENAI_API_KEY not set"}
    client = OpenAI(api_key=key)
    t0 = time.time()
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    latency = time.time() - t0
    text = resp.choices[0].message.content or ""
    usage = resp.usage
    tokens_in  = usage.prompt_tokens if usage else 0
    tokens_out = usage.completion_tokens if usage else 0
    price = PRICING.get(model, {"input": 0, "output": 0})
    cost  = (tokens_in * price["input"] + tokens_out * price["output"]) / 1_000_000
    return {"output": text, "latency": latency, "tokens_in": tokens_in, "tokens_out": tokens_out, "cost": cost}


def run_gemini(model: str, prompt: str) -> dict:
    try:
        import google.generativeai as genai
    except ImportError:
        return {"error": "google-generativeai package not installed. Run: pip install google-generativeai"}
    key = os.getenv("GOOGLE_API_KEY")
    if not key:
        return {"error": "GOOGLE_API_KEY not set"}
    genai.configure(api_key=key)
    m = genai.GenerativeModel(model)
    t0 = time.time()
    resp = m.generate_content(prompt)
    latency = time.time() - t0
    text = getattr(resp, "text", "") or ""
    return {"output": text, "latency": latency, "tokens_in": None, "tokens_out": None, "cost": None}


def run_model(model: str, prompt: str) -> dict:
    if model in CLAUDE_MODELS:
        return run_claude(model, prompt)
    if model in OPENAI_MODELS:
        return run_openai(model, prompt)
    if model in GEMINI_MODELS:
        return run_gemini(model, prompt)
    return {"error": f"Unknown model: {model}"}


# ── LLM judge ────────────────────────────────────────────────────────────────

JUDGE_PROMPT = """\
You are an objective evaluator. Score the following AI response on a scale of 1-10.

Criteria:
- Relevance: Does it directly address the prompt?
- Accuracy: Are the facts/numbers correct based on the data?
- Completeness: Does it cover what was asked?
- Clarity: Is it well-structured and easy to read?

Respond with ONLY a JSON object like this (no markdown, no explanation):
{{"score": 8, "reason": "One sentence justification."}}

--- PROMPT ---
{prompt}

--- RESPONSE TO SCORE ---
{response}
"""

def judge_output(prompt: str, response: str) -> tuple[float, str]:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return (0.0, "ANTHROPIC_API_KEY not set — cannot judge")
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        msg = client.messages.create(
            model=JUDGE_MODEL,
            max_tokens=200,
            temperature=0,
            messages=[{"role": "user", "content": JUDGE_PROMPT.format(prompt=prompt, response=response)}],
        )
        raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        data = json.loads(raw)
        return (float(data["score"]), data.get("reason", ""))
    except Exception as e:
        return (0.0, f"Judge error: {e}")


# ── Scoring helpers ───────────────────────────────────────────────────────────

def normalize(values: list[Optional[float]], invert: bool = False) -> list[Optional[float]]:
    valid = [v for v in values if v is not None]
    if not valid or max(valid) == min(valid):
        return [5.0 if v is not None else None for v in values]
    lo, hi = min(valid), max(valid)
    result = []
    for v in values:
        if v is None:
            result.append(None)
        else:
            norm = (v - lo) / (hi - lo)
            score = (1 - norm if invert else norm) * 10
            result.append(round(score, 2))
    return result


def fmt_score(v: Optional[float]) -> str:
    return f"{v:.1f}/10" if v is not None else "n/a"

def fmt_cost(v: Optional[float]) -> str:
    if v is None: return "n/a"
    if v < 0.001: return f"${v*1000:.4f}m"
    return f"${v:.5f}"


# ── Display ───────────────────────────────────────────────────────────────────

def print_table(rows: list[dict], cols: list[str] = None):
    if cols is None:
        cols = ["Model", "Quality", "Speed", "Cost", "Overall", "Tokens (in/out)", "Cost ($)"]
    widths = [max(len(c), max((len(str(r.get(c, ""))) for r in rows), default=0)) for c in cols]

    sep    = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    header = "|" + "|".join(f" {c:<{w}} " for c, w in zip(cols, widths)) + "|"

    print("\n" + sep)
    print(header)
    print(sep)
    for r in rows:
        line = "|" + "|".join(f" {str(r.get(c,'')):<{w}} " for c, w in zip(cols, widths)) + "|"
        print(line)
    print(sep + "\n")


# ── Results persistence ───────────────────────────────────────────────────────

def save_run(prompt_name: str, models: list[str], rows: list[dict], prompt_text: str, data_file: str) -> str:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    model_slug = "+".join(m.split("-")[1] for m in models)[:40]  # e.g. "sonnet+haiku"
    filename = f"{ts}_{prompt_name}_{model_slug}.json"
    path = os.path.join(RESULTS_DIR, filename)

    export = {
        "timestamp": ts,
        "prompt_name": prompt_name,
        "data_file": data_file or "",
        "models": models,
        "results": [
            {
                "model":        row["Model"],
                "quality":      row["Quality"],
                "speed":        row["Speed"],
                "cost_score":   row["Cost"],
                "overall":      row["Overall"],
                "tokens":       row["Tokens (in/out)"],
                "cost_usd":     row["Cost ($)"],
                "quality_raw":  row["_quality_raw"],
                "overall_raw":  row["_overall_raw"],
                "latency_s":    row["_latency"],
                "judge_reason": row["_reason"],
                "output":       row["_output"],
            }
            for row in rows
        ],
    }
    with open(path, "w") as f:
        json.dump(export, f, indent=2)
    return path


def load_runs() -> list[dict]:
    if not os.path.isdir(RESULTS_DIR):
        return []
    runs = []
    for fname in sorted(os.listdir(RESULTS_DIR), reverse=True):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(RESULTS_DIR, fname)
        try:
            with open(path) as f:
                data = json.load(f)
            data["_file"] = fname
            data["_path"] = path
            runs.append(data)
        except Exception:
            pass
    return runs


# ── --list ────────────────────────────────────────────────────────────────────

def cmd_list():
    runs = load_runs()
    if not runs:
        print("\nNo runs yet. Run a benchmark first.\n")
        return

    rows = []
    for i, run in enumerate(runs, 1):
        best = max(run["results"], key=lambda r: r.get("overall_raw") or 0, default={})
        rows.append({
            "#":           str(i),
            "Timestamp":   run.get("timestamp", ""),
            "Prompt":      run.get("prompt_name", ""),
            "Models":      ", ".join(run.get("models", [])),
            "Best model":  best.get("model", ""),
            "Overall":     best.get("overall", ""),
            "File":        run.get("_file", ""),
        })

    print_table(rows, cols=["#", "Timestamp", "Prompt", "Models", "Best model", "Overall"])
    print("Use --compare results/<file1> results/<file2> to compare two runs.\n")


# ── --compare ─────────────────────────────────────────────────────────────────

def cmd_compare(path_a: str, path_b: str):
    for p in (path_a, path_b):
        if not os.path.isfile(p):
            print(f"File not found: {p}")
            sys.exit(1)

    with open(path_a) as f: run_a = json.load(f)
    with open(path_b) as f: run_b = json.load(f)

    print(f"\nComparing:")
    print(f"  A: {os.path.basename(path_a)}  (prompt: {run_a.get('prompt_name')})")
    print(f"  B: {os.path.basename(path_b)}  (prompt: {run_b.get('prompt_name')})")

    # Build lookup by model
    a_by_model = {r["model"]: r for r in run_a["results"]}
    b_by_model = {r["model"]: r for r in run_b["results"]}
    all_models = sorted(set(a_by_model) | set(b_by_model))

    rows = []
    for model in all_models:
        ra = a_by_model.get(model)
        rb = b_by_model.get(model)

        def val(r, key): return r.get(key, "—") if r else "—"
        def delta(r_a, r_b, key):
            va = r_a.get(key) if r_a else None
            vb = r_b.get(key) if r_b else None
            if va is None or vb is None: return ""
            d = vb - va
            return f"+{d:.1f}" if d > 0 else f"{d:.1f}"

        rows.append({
            "Model":       model,
            "Quality A":   val(ra, "quality"),
            "Quality B":   val(rb, "quality"),
            "Δ Quality":   delta(ra, rb, "quality_raw"),
            "Overall A":   val(ra, "overall"),
            "Overall B":   val(rb, "overall"),
            "Δ Overall":   delta(ra, rb, "overall_raw"),
            "Cost A":      val(ra, "cost_usd"),
            "Cost B":      val(rb, "cost_usd"),
        })

    print_table(rows, cols=["Model", "Quality A", "Quality B", "Δ Quality", "Overall A", "Overall B", "Δ Overall", "Cost A", "Cost B"])

    # Show judge reasons for each
    for label, run in [("A", run_a), ("B", run_b)]:
        print(f"── Judge reasons ({label}: {run.get('prompt_name')}) ──")
        for r in run["results"]:
            if r.get("judge_reason"):
                print(f"  {r['model']}: {r['judge_reason']}")
        print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Benchmark a prompt across multiple LLMs")
    parser.add_argument("--prompt",   help="Prompt text or path to a .txt file")
    parser.add_argument("--file",     help="Path to a CSV file to include as context")
    parser.add_argument("--models",   default=",".join(DEFAULT_MODELS),
                        help=f"Comma-separated models, or: all, claude, openai, gemini. Available: {', '.join(PRICING)}")
    parser.add_argument("--no-judge", action="store_true", help="Skip quality scoring")
    parser.add_argument("--list",     action="store_true", help="List all past runs")
    parser.add_argument("--compare",  nargs=2, metavar=("RUN_A", "RUN_B"), help="Compare two saved runs")
    args = parser.parse_args()

    if args.list:
        cmd_list()
        return

    if args.compare:
        cmd_compare(args.compare[0], args.compare[1])
        return

    if not args.prompt:
        parser.print_help()
        sys.exit(1)

    # ── Load prompt
    if os.path.isfile(args.prompt):
        prompt_name = os.path.splitext(os.path.basename(args.prompt))[0]
        with open(args.prompt) as f:
            prompt_text = f.read().strip()
    else:
        prompt_name = "inline"
        prompt_text = args.prompt.strip()

    # ── Load CSV
    if args.file:
        if not os.path.isfile(args.file):
            print(f"Error: file not found: {args.file}")
            sys.exit(1)
        with open(args.file) as f:
            csv_context = f.read().strip()
        prompt_text = f"{prompt_text}\n\nData:\n{csv_context}"

    # ── Resolve models
    if args.models.lower() == "all":
        models = list(PRICING.keys())
    elif args.models.lower() == "claude":
        models = CLAUDE_MODELS
    elif args.models.lower() == "openai":
        models = OPENAI_MODELS
    elif args.models.lower() == "gemini":
        models = GEMINI_MODELS
    else:
        models = [m.strip() for m in args.models.split(",")]

    print(f"\nRunning {len(models)} model(s)...\n")

    # ── Run models
    results = {}
    for model in models:
        print(f"  → {model}...", end=" ", flush=True)
        r = run_model(model, prompt_text)
        if "error" in r:
            print(f"FAILED ({r['error']})")
        else:
            print(f"done ({r['latency']:.1f}s)")
        results[model] = r

    successful = {m: r for m, r in results.items() if "error" not in r}

    if not successful:
        print("\nAll models failed. Check your API keys.")
        sys.exit(1)

    # ── Judge quality
    quality_scores = {}
    quality_reasons = {}
    if not args.no_judge:
        print(f"\nJudging quality with {JUDGE_MODEL}...\n")
        for model, r in successful.items():
            print(f"  → scoring {model}...", end=" ", flush=True)
            score, reason = judge_output(prompt_text, r["output"])
            quality_scores[model] = score
            quality_reasons[model] = reason
            print(f"{score:.1f}/10")

    # ── Compute speed + cost scores
    model_list   = list(successful.keys())
    latencies    = [successful[m]["latency"] for m in model_list]
    costs        = [successful[m]["cost"] for m in model_list]
    speed_scores = normalize(latencies, invert=True)
    cost_scores  = normalize(costs, invert=True)
    speed_map    = dict(zip(model_list, speed_scores))
    cost_map     = dict(zip(model_list, cost_scores))

    # ── Build rows
    rows = []
    for model in model_list:
        r = successful[model]
        q = quality_scores.get(model)
        s = speed_map.get(model)
        c = cost_map.get(model)

        weights = []
        if q is not None: weights.append((q, 0.50))
        if s is not None: weights.append((s, 0.25))
        if c is not None: weights.append((c, 0.25))
        total_w = sum(w for _, w in weights)
        overall = sum(v * w for v, w in weights) / total_w if weights else None

        tok_str = f"{r['tokens_in']}/{r['tokens_out']}" if r.get("tokens_in") else "n/a"

        rows.append({
            "Model":           model,
            "Quality":         fmt_score(q),
            "Speed":           fmt_score(s),
            "Cost":            fmt_score(c),
            "Overall":         fmt_score(overall),
            "Tokens (in/out)": tok_str,
            "Cost ($)":        fmt_cost(r.get("cost")),
            "_quality_raw":    q,
            "_overall_raw":    overall,
            "_reason":         quality_reasons.get(model, ""),
            "_output":         r["output"],
            "_latency":        r["latency"],
        })

    rows.sort(key=lambda x: x.get("_overall_raw") or 0, reverse=True)

    # ── Print table
    print_table(rows)

    # ── Print outputs
    print("=" * 70)
    print("OUTPUTS")
    print("=" * 70)
    for row in rows:
        print(f"\n── {row['Model']} (Quality: {row['Quality']}, Overall: {row['Overall']}) ──")
        if row.get("_reason"):
            print(f"Judge: {row['_reason']}")
        print()
        print(row["_output"])

    # ── Auto-save
    saved_path = save_run(prompt_name, model_list, rows, prompt_text, args.file or "")
    print(f"\n✓ Run saved to {saved_path}")
    print(f"  View all runs: python bench.py --list")


if __name__ == "__main__":
    main()
