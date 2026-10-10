-- Analysis queries for evals.db.  Run:  sqlite3 evals.db < queries.sql   (or paste one into any SQLite client)

-- 1. Pass rate and cost per passing answer, by prompt and model (latest batch)
SELECT prompt_name, model, COUNT(*) AS runs, SUM(passed) AS passed,
       ROUND(100.0 * SUM(passed) / COUNT(*), 1) AS pass_pct,
       ROUND(SUM(cost_usd) / NULLIF(SUM(passed), 0), 4) AS usd_per_passing_answer,
       ROUND(AVG(latency_ms)) AS avg_latency_ms
FROM runs WHERE batch_id = (SELECT batch_id FROM batches ORDER BY created_at DESC LIMIT 1) AND error IS NULL
GROUP BY prompt_name, model ORDER BY pass_pct DESC;

-- 2. Which checks fail most (failure modes), latest batch
SELECT prompt_name, model, check_name, SUM(check_result = 0) AS failed, COUNT(*) AS applicable
FROM run_checks WHERE batch_id = (SELECT batch_id FROM batches ORDER BY created_at DESC LIMIT 1)
GROUP BY prompt_name, model, check_name HAVING failed > 0 ORDER BY failed DESC;

-- 3. Flaky cases: same prompt, model and case, different outcome across repetitions (instability, not wrongness)
SELECT prompt_name, model, case_id, COUNT(*) AS reps, SUM(passed) AS n_passed
FROM runs WHERE error IS NULL GROUP BY prompt_name, model, case_id HAVING SUM(passed) > 0 AND SUM(passed) < COUNT(*)
ORDER BY prompt_name, model, case_id;

-- 4. Cases no prompt solves (candidates for a better case description, or a real model limit)
SELECT case_id, COUNT(*) AS runs, SUM(passed) AS n_passed FROM runs WHERE error IS NULL
GROUP BY case_id HAVING SUM(passed) = 0 ORDER BY case_id;

-- 5. Prompt A vs prompt B on the same cases (per-case pass rates side by side; feed to `promptbench compare`)
SELECT a.case_id, ROUND(AVG(a.passed), 2) AS a_rate, ROUND(AVG(b.passed), 2) AS b_rate
FROM runs a JOIN runs b ON a.case_id = b.case_id AND a.model = b.model AND a.batch_id = b.batch_id
WHERE a.prompt_name = 'analyze_v1' AND b.prompt_name = 'analyze_v6' AND a.error IS NULL AND b.error IS NULL
GROUP BY a.case_id ORDER BY a.case_id;

-- 6. Quality per dollar: does the cheaper model clear the bar?
SELECT model, ROUND(100.0 * SUM(passed) / COUNT(*), 1) AS pass_pct, ROUND(SUM(cost_usd), 4) AS total_usd,
       ROUND(SUM(cost_usd) / COUNT(*) * 1000, 3) AS usd_per_1000_calls
FROM runs WHERE error IS NULL GROUP BY model ORDER BY pass_pct DESC;
