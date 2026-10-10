import argparse
import sys

from . import golden, report, runner


def main(argv=None):
    p = argparse.ArgumentParser(prog="promptbench", description="Eval harness: golden set, deterministic graders, SQL run log, A/B stats")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("golden", help="(re)generate the seeded golden set")
    r = sub.add_parser("run", help="run prompts x models x cases x reps and log results")
    r.add_argument("--prompts", required=True, help="comma-separated prompt names from prompts/ (without .txt)")
    r.add_argument("--models", required=True, help="comma-separated model ids, e.g. claude-haiku-4-5 or mock:good")
    r.add_argument("--reps", type=int, default=3)
    r.add_argument("--cases", help="comma-separated case ids (default: all)")
    r.add_argument("--label")
    s = sub.add_parser("report", help="pass rates, cost, failure modes for a batch (default: latest)")
    s.add_argument("--batch")
    c = sub.add_parser("compare", help="paired A/B comparison of two prompts on one model")
    c.add_argument("a")
    c.add_argument("b")
    c.add_argument("--model", required=True)
    c.add_argument("--batch")
    g = sub.add_parser("regress", help="exit 1 if pass rate fell more than --max-drop versus a baseline batch")
    g.add_argument("baseline")
    g.add_argument("current")
    g.add_argument("--max-drop", type=float, default=0.10)
    a = p.parse_args(argv)

    if a.cmd == "golden":
        print(f"wrote {golden.generate()} cases to golden/")
    elif a.cmd == "run":
        b = runner.run_batch(a.prompts.split(","), a.models.split(","), reps=a.reps, label=a.label,
                             case_ids=a.cases.split(",") if a.cases else None)
        print(f"batch: {b}")
    elif a.cmd == "report":
        print(report.summary(a.batch))
    elif a.cmd == "compare":
        print(report.compare(a.a, a.b, a.model, a.batch)[0])
    elif a.cmd == "regress":
        failed, text = report.regress(a.baseline, a.current, a.max_drop)
        print(text)
        sys.exit(1 if failed else 0)
