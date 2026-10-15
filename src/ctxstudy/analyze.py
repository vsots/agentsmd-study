"""Turn trials.jsonl files into the numbers the write-up will report.

Primary outcome (pre-registered in ANALYSIS_PLAN.md): the per-task difference in
pass rate, with vs without context files, averaged over tasks, with a task-level
bootstrap 95% CI and a sign test across tasks.

Secondary outcomes: speed (wall-clock seconds, turns), tokens and the estimated
cost, each as a with/without ratio of per-task medians (geometric mean across
tasks, bootstrap CI). Tail behaviors are reported as shares of trials.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

from ctxstudy.stats import bootstrap_ci, geo_mean, mean, median, sign_test, wilson

ARMS = ("with", "without")
EFFICIENCY_METRICS = {
    "wall_seconds": "Wall-clock seconds",
    "num_turns": "Agent turns",
    "output_tokens": "Output tokens",
    "total_cost_usd": "Estimated cost (USD)",
}


def load_trials(run_dirs: list[Path]) -> tuple[list[dict], list[dict]]:
    """Returns (usable trials, excluded trials). Exclusions are reported, never hidden."""
    usable, excluded = [], []
    for d in run_dirs:
        path = d / "trials.jsonl"
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            rec = json.loads(line)
            (excluded if rec.get("infra_error") or rec.get("score") is None else usable).append(rec)
    return usable, excluded


def _by_task(trials: list[dict]) -> dict[tuple[str, str], dict[str, list[dict]]]:
    grouped: dict[tuple[str, str], dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in trials:
        grouped[(r["repo"], r["task_id"])][r["arm"]].append(r)
    return grouped


def correctness(trials: list[dict]) -> dict:
    out: dict = {}
    for arm in ARMS:
        recs = [r for r in trials if r["arm"] == arm]
        k, n = sum(bool(r["score"]["passed"]) for r in recs), len(recs)
        out[arm] = {"n": n, "passed": k, "rate": k / n if n else math.nan, "ci": wilson(k, n)}
    diffs = []
    for arms in _by_task(trials).values():
        if all(arms.get(a) for a in ARMS):
            rate = {a: mean([float(bool(r["score"]["passed"])) for r in arms[a]]) for a in ARMS}
            diffs.append(rate["with"] - rate["without"])
    out["tasks_compared"] = len(diffs)
    out["mean_diff"] = mean(diffs)
    out["mean_diff_ci"] = bootstrap_ci(diffs, mean)
    out["helped"] = sum(d > 0 for d in diffs)
    out["hurt"] = sum(d < 0 for d in diffs)
    out["sign_test_p"] = sign_test(out["helped"], out["hurt"])
    return out


def efficiency(trials: list[dict], metric: str) -> dict:
    ratios, overall = [], {a: [] for a in ARMS}
    for arms in _by_task(trials).values():
        per_arm = {}
        for a in ARMS:
            vals = [(r.get("agent") or {}).get(metric) for r in arms.get(a, [])]
            vals = [float(v) for v in vals if isinstance(v, (int, float)) and v > 0]
            per_arm[a] = vals
            overall[a].extend(vals)
        if per_arm["with"] and per_arm["without"]:
            ratios.append(median(per_arm["with"]) / median(per_arm["without"]))
    return {
        "median_with": median(overall["with"]),
        "median_without": median(overall["without"]),
        "ratio": geo_mean(ratios),          # < 1 means the file made it smaller/faster/cheaper
        "ratio_ci": bootstrap_ci(ratios, geo_mean),
        "tasks_compared": len(ratios),
    }


def tails(trials: list[dict]) -> dict:
    out = {}
    for arm in ARMS:
        recs = [r for r in trials if r["arm"] == arm]
        n = len(recs) or 1
        agent = [(r.get("agent") or {}) for r in recs]
        changes = [(r.get("changes") or {}) for r in recs]
        out[arm] = {
            "agent_error": sum(bool(a.get("is_error")) for a in agent) / n,
            "hit_max_turns": sum(a.get("subtype") == "error_max_turns" for a in agent) / n,
            "timed_out": sum(bool(a.get("timed_out")) for a in agent) / n,
            "ran_tests": sum(bool(a.get("ran_tests")) for a in agent) / n,
            "edited_tests": sum(bool(c.get("touched_tests")) for c in changes) / n,
            "edited_context_files": sum(bool(c.get("touched_context_files")) for c in changes) / n,
        }
    return out


def _pct(x: float) -> str:
    return "n/a" if x != x else f"{100 * x:.1f}%"


def _num(x: float, digits: int = 2) -> str:
    return "n/a" if x != x else f"{x:.{digits}f}"


def report(trials: list[dict], excluded: list[dict], title: str) -> str:
    lines = [f"# {title}", "",
             f"{len(trials)} usable trials, {len(excluded)} excluded (infrastructure errors).", ""]
    groups = {"All repos": trials}
    for repo in sorted({t["repo"] for t in trials}):
        groups[repo] = [t for t in trials if t["repo"] == repo]
    for name, group in groups.items():
        c = correctness(group)
        lines += [f"## {name}", "",
                  "| Arm | Trials | Passed | Pass rate | 95% CI |", "| --- | --- | --- | --- | --- |"]
        for arm in ARMS:
            a = c[arm]
            lines.append(f"| {arm} | {a['n']} | {a['passed']} | {_pct(a['rate'])} | "
                         f"{_pct(a['ci'][0])} to {_pct(a['ci'][1])} |")
        lo, hi = c["mean_diff_ci"]
        lines += ["",
                  f"Per-task difference (with minus without): {_num(100 * c['mean_diff'], 1)} points "
                  f"(95% CI {_num(100 * lo, 1)} to {_num(100 * hi, 1)}) across {c['tasks_compared']} tasks. "
                  f"Helped on {c['helped']}, hurt on {c['hurt']} (sign test p = {_num(c['sign_test_p'], 3)}).",
                  "", "| Metric | Median with | Median without | With/without ratio | 95% CI |",
                  "| --- | --- | --- | --- | --- |"]
        for key, label in EFFICIENCY_METRICS.items():
            e = efficiency(group, key)
            lines.append(f"| {label} | {_num(e['median_with'])} | {_num(e['median_without'])} | "
                         f"{_num(e['ratio'])} | {_num(e['ratio_ci'][0])} to {_num(e['ratio_ci'][1])} |")
        t = tails(group)
        lines += ["", "| Tail behavior | with | without |", "| --- | --- | --- |"]
        for key in t["with"]:
            lines.append(f"| {key.replace('_', ' ')} | {_pct(t['with'][key])} | {_pct(t['without'][key])} |")
        lines.append("")
    return "\n".join(lines)
