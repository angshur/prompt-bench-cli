"""Run prompts x models x golden cases x repetitions, grade deterministically, log to SQLite."""
import datetime
import hashlib
import json
import os
import subprocess
import uuid

from . import db, golden, graders, providers

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPTS_DIR = os.path.join(HERE, "prompts")


def load_prompt(name):
    with open(os.path.join(PROMPTS_DIR, name + ".txt")) as f:
        text = f.read().strip()
    meta_path = os.path.join(PROMPTS_DIR, name + ".meta.json")
    meta = {}
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)
    return text, meta


def git_sha():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=HERE, stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return None


def build_prompt(text, csv_text):
    return f"{text}\n\nData:\n{csv_text.strip()}"   # same injection format as bench.py


def run_batch(prompts, models, reps=3, label=None, case_ids=None, judge=False, log=print):
    con = db.connect()
    cases = golden.load()
    if case_ids:
        cases = [c for c in cases if c["case_id"] in case_ids]
    batch_id = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    con.execute("INSERT INTO batches(batch_id, created_at, label, git_sha, reps) VALUES(?,?,?,?,?)",
                (batch_id, datetime.datetime.utcnow().isoformat(), label, git_sha(), reps))
    total = len(prompts) * len(models) * len(cases) * reps
    done = 0
    for pname in prompts:
        text, meta = load_prompt(pname)
        phash = hashlib.sha256(text.encode()).hexdigest()[:12]
        for model in models:
            for case in cases:
                full = build_prompt(text, case["csv"])
                for rep in range(reps):
                    done += 1
                    r = providers.call(model, full, case=case, prompt_meta=meta)
                    if r.get("error"):
                        con.execute("INSERT INTO runs(batch_id,prompt_name,prompt_hash,model,case_id,rep,error) VALUES(?,?,?,?,?,?,?)",
                                    (batch_id, pname, phash, model, case["case_id"], rep, r["error"]))
                        log(f"[{done}/{total}] {pname} {model} {case['case_id']} ERROR {r['error'][:80]}")
                        continue
                    g = graders.grade(r["output"], case, meta)
                    con.execute(
                        "INSERT INTO runs(batch_id,prompt_name,prompt_hash,model,case_id,rep,output,latency_ms,tokens_in,tokens_out,cost_usd,passed,score,ungrounded,checks_json) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (batch_id, pname, phash, model, case["case_id"], rep, r["output"], r.get("latency_ms"), r.get("tokens_in"),
                         r.get("tokens_out"), r.get("cost"), 1 if g["passed"] else 0, g["score"], g["ungrounded"], json.dumps(g["checks"])))
                    log(f"[{done}/{total}] {pname} {model} {case['case_id']} rep{rep} {'PASS' if g['passed'] else 'FAIL'}")
                con.commit()
    con.commit()
    return batch_id
