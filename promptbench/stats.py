"""Small-sample statistics for comparing two prompts (or models) on the same golden cases. Stdlib only."""
import itertools
import math
import random


def wilson(k, n, z=1.96):
    """Wilson score interval for a pass rate. Better than +/- sqrt(p(1-p)/n) at small n and near 0 or 1."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - r) / d, (c + r) / d)


def paired_compare(a, b, resamples=10000, seed=11):
    """a, b: per-case mean pass rates (same case order). Returns mean difference b-a, 95% bootstrap CI over cases,
    and a sign-flip permutation p-value (exact when there are 16 or fewer non-tied cases)."""
    diffs = [y - x for x, y in zip(a, b)]
    n = len(diffs)
    mean = sum(diffs) / n if n else 0.0
    rng = random.Random(seed)
    boots = sorted(sum(rng.choice(diffs) for _ in range(n)) / n for _ in range(resamples)) if n else [0.0]
    lo, hi = boots[int(0.025 * resamples)], boots[int(0.975 * resamples) - 1]
    nz = [d for d in diffs if d != 0]
    if not nz:
        p = 1.0
    elif len(nz) <= 16:
        obs = abs(sum(nz))
        hits = 0
        for signs in itertools.product((1, -1), repeat=len(nz)):
            if abs(sum(s * abs(d) for s, d in zip(signs, nz))) >= obs - 1e-12:
                hits += 1
        p = hits / 2 ** len(nz)
    else:
        obs = abs(sum(nz))
        hits = sum(1 for _ in range(20000) if abs(sum(rng.choice((1, -1)) * abs(d) for d in nz)) >= obs - 1e-12)
        p = (hits + 1) / 20001
    return {"n_cases": n, "mean_diff": mean, "ci_low": lo, "ci_high": hi, "p_value": p,
            "cases_better": sum(1 for d in diffs if d > 0), "cases_worse": sum(1 for d in diffs if d < 0), "cases_tied": sum(1 for d in diffs if d == 0)}


def verdict(res, alpha=0.05):
    if res["p_value"] < alpha and res["ci_low"] > 0:
        return "B is better"
    if res["p_value"] < alpha and res["ci_high"] < 0:
        return "A is better"
    return "no reliable difference at this sample size"
