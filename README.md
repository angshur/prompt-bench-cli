# prompt-bench-cli

A small eval harness for one question product teams ask constantly: **did that prompt or model change make the product better, or did we just get lucky on one example?**

It started (March 2026) as a CLI that ran one prompt and one CSV through several models and scored them on quality (an LLM judge), speed and cost. I used it to iterate one analysis prompt through six versions. The weakness was obvious in hindsight: one 10-row CSV, one judge, one run per version. A score of 7 vs 6 told me nothing about whether v6 was really better than v1.

v0.2 fixes that:

| Piece | What it does |
|---|---|
| **Golden set** (`promptbench/golden.py`) | 12 seeded synthetic marketing datasets with edge cases: a cost spike, a campaign with spend and zero conversions, blank cells, near-ties on cost per conversion, collapsing conversions, one campaign only, large numbers. The right answers are *computed*, not guessed (`golden/truth.json`). |
| **Deterministic graders** (`promptbench/graders.py`) | Pass/fail checks from ground truth, no LLM involved: leads with the right campaign, names the anomaly with campaign and date, calls out zero-conversion spend, acknowledges blanks, cost-per-conversion figures within 2%, required sections in order. Also counts numbers that match nothing in the data. |
| **SQL run log** (`promptbench/db.py`, `queries.sql`) | Every (prompt, model, case, repetition) is a row in SQLite with output, latency, tokens, cost and each check result. Analysis is plain SQL. |
| **A/B statistics** (`promptbench/stats.py`) | Wilson intervals on pass rates; paired comparison of two prompts over the same cases with a bootstrap CI and an exact sign-flip permutation test. Says "no reliable difference" when that's the truth. |
| **Regression gate** (`promptbench regress`) | Exits 1 if a pass rate drops more than a threshold versus a baseline batch. Wire it into CI. |

## Quick start
```bash
pip install anthropic            # plus openai / google-generativeai if you use those models
export ANTHROPIC_API_KEY=...     # never commit keys

python -m promptbench golden                                   # regenerate the golden set (deterministic)
python -m promptbench run --prompts analyze_v1,analyze_v6 --models claude-haiku-4-5 --reps 3 --label first
python -m promptbench report                                   # pass rate (95% CI), $ per passing answer, failure modes
python -m promptbench compare analyze_v1 analyze_v6 --model claude-haiku-4-5
python -m promptbench regress <baseline_batch> <current_batch> --max-drop 0.10
sqlite3 evals.db < queries.sql                                 # more cuts: flaky cases, unsolved cases, quality per dollar
```
Test without spending anything: `--models mock:good,mock:lazy` (deterministic fake models used by the unit tests; their results are never real results).

The original single-run CLI is still here: `python bench.py --prompt prompts/analyze_v1.txt --file golden/data/c01_plain.csv`.

## How a prompt declares its rules
`prompts/<name>.txt` is sent to the model. An optional `prompts/<name>.meta.json` tells the grader what that version promised: required `sections`, a `cpa_every_entry` rule, or checks to `skip` (a prompt that never asks which campaign is best should not be failed for it).

## Design choices worth knowing
- **Outcome checks, not style checks.** The graders test whether the brief is *correct and grounded* on a shared task, so different prompt styles are comparable.
- **Ground truth from code.** `golden.py` computes best/worst campaign, totals and injected anomalies, so a failure is a real failure.
- **Repetitions matter.** LLM outputs vary run to run; `--reps 3` turns one lucky answer into a rate with an interval.
- **Cost is per passing answer**, not per call: a cheap model that fails half the time is not cheap.

## Limits (read before trusting a number)
- The graders are heuristics. "Leads with the right campaign" means the first campaign named in the answer; a prompt that opens with a different framing can be graded unfairly (use `skip`).
- 12 synthetic cases cover the failure modes I care about, not the real distribution of dashboards. Small n means wide intervals; the stats report them.
- Deterministic checks do not judge writing quality or usefulness. Use the LLM judge in `bench.py` for that, alongside, not instead.
- Pricing in `bench.py` is a static table; update it before reading cost numbers.

## Tests
`python -m unittest discover -s tests -v` (11 tests: graders catch what they should, the golden set is deterministic, the statistics behave, the pipeline runs end to end). CI runs them on Python 3.9 and 3.12.

## Results
Full runs against real models are not published yet. They land in `RESULTS.md` once run (prompt versions v1 to v6, two model tiers, 3 repetitions).
