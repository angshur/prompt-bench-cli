"""Reports from the run log: pass rates with intervals, cost per passing answer, failure modes, A/B comparison, regression gate."""
from . import db, stats


def _rows(con, sql, args=()):
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def summary(batch_id=None):
    con = db.connect()
    batch_id = batch_id or con.execute("SELECT batch_id FROM batches ORDER BY created_at DESC LIMIT 1").fetchone()[0]
    rows = _rows(con, """SELECT prompt_name, model, COUNT(*) n, SUM(passed) k, ROUND(AVG(score),3) avg_checks,
                         ROUND(AVG(latency_ms)) lat_ms, ROUND(SUM(cost_usd),4) cost, ROUND(AVG(ungrounded),2) ungrounded
                         FROM runs WHERE batch_id=? AND error IS NULL GROUP BY prompt_name, model ORDER BY prompt_name, model""", (batch_id,))
    out = [f"Batch {batch_id}", "", "| prompt | model | runs | pass rate (95% CI) | avg checks | latency ms | $ per passing answer | ungrounded nums |", "|---|---|---:|---|---:|---:|---:|---:|"]
    for r in rows:
        lo, hi = stats.wilson(r["k"], r["n"])
        cpp = f"{r['cost'] / r['k']:.4f}" if r["k"] and r["cost"] else "n/a"
        out.append(f"| {r['prompt_name']} | {r['model']} | {r['n']} | {r['k']}/{r['n']} = {r['k'] / r['n']:.0%} ({lo:.0%} to {hi:.0%}) | {r['avg_checks']} | {int(r['lat_ms'] or 0)} | {cpp} | {r['ungrounded']} |")
    fm = _rows(con, """SELECT prompt_name, model, check_name, SUM(CASE WHEN check_result=0 THEN 1 ELSE 0 END) failed, COUNT(*) total
                       FROM run_checks WHERE batch_id=? GROUP BY prompt_name, model, check_name HAVING failed>0 ORDER BY failed DESC""", (batch_id,))
    if fm:
        out += ["", "Failure modes (checks that failed):", "", "| prompt | model | check | failed / applicable |", "|---|---|---|---:|"]
        out += [f"| {r['prompt_name']} | {r['model']} | {r['check_name']} | {r['failed']}/{r['total']} |" for r in fm]
    return "\n".join(out)


def _case_rates(con, batch_id, prompt, model):
    rows = _rows(con, "SELECT case_id, AVG(passed) p FROM runs WHERE batch_id=? AND prompt_name=? AND model=? AND error IS NULL GROUP BY case_id ORDER BY case_id",
                 (batch_id, prompt, model))
    return {r["case_id"]: r["p"] for r in rows}


def compare(prompt_a, prompt_b, model, batch_id=None):
    con = db.connect()
    batch_id = batch_id or con.execute("SELECT batch_id FROM batches ORDER BY created_at DESC LIMIT 1").fetchone()[0]
    a, b = _case_rates(con, batch_id, prompt_a, model), _case_rates(con, batch_id, prompt_b, model)
    ids = sorted(set(a) & set(b))
    res = stats.paired_compare([a[i] for i in ids], [b[i] for i in ids])
    lines = [f"A = {prompt_a}, B = {prompt_b}, model = {model}, batch {batch_id}, {res['n_cases']} paired cases",
             f"Mean pass-rate difference (B - A): {res['mean_diff']:+.1%}  (95% bootstrap CI {res['ci_low']:+.1%} to {res['ci_high']:+.1%})",
             f"Cases B better / worse / tied: {res['cases_better']} / {res['cases_worse']} / {res['cases_tied']}",
             f"Sign-flip permutation p-value: {res['p_value']:.3f}",
             f"Verdict: {stats.verdict(res)}"]
    return "\n".join(lines), res


def regress(baseline_batch, current_batch, max_drop=0.10):
    """Exit-code gate: for every (prompt, model) in both batches, fail if the pass rate fell by more than max_drop."""
    con = db.connect()
    q = "SELECT prompt_name, model, AVG(passed) p, COUNT(*) n FROM runs WHERE batch_id=? AND error IS NULL GROUP BY prompt_name, model"
    base = {(r["prompt_name"], r["model"]): r for r in _rows(con, q, (baseline_batch,))}
    cur = {(r["prompt_name"], r["model"]): r for r in _rows(con, q, (current_batch,))}
    failed, lines = False, []
    for key in sorted(set(base) & set(cur)):
        d = cur[key]["p"] - base[key]["p"]
        bad = d < -max_drop
        failed = failed or bad
        lines.append(f"{'FAIL' if bad else 'ok  '} {key[0]} / {key[1]}: {base[key]['p']:.0%} -> {cur[key]['p']:.0%} ({d:+.0%})")
    if not lines:
        lines.append("No overlapping (prompt, model) pairs between the two batches.")
        failed = True
    return failed, "\n".join(lines)
