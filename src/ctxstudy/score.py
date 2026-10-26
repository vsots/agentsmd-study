"""Run the hidden tests and decide pass/fail. Deterministic: no AI judge.

A trial passes when every fail-to-pass test now passes (the agent fixed the
problem) and every pass-to-pass test still passes (it broke nothing nearby).
"""

from __future__ import annotations

import shlex
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from ctxstudy.config import RepoConfig
from ctxstudy.executor import AGENT_USER, Executor
from ctxstudy.tasks import Task
from ctxstudy.workspace import apply_patch, reset_test_files, runnable_test_files


@dataclass
class TestRun:
    rc: int
    results: dict[str, str] = field(default_factory=dict)  # test id -> passed/failed/skipped
    timed_out: bool = False
    note: str = ""


def parse_junit(xml_text: str) -> dict[str, str]:
    results: dict[str, str] = {}
    if not xml_text.strip():
        return results
    root = ET.fromstring(xml_text)
    for case in root.iter("testcase"):
        test_id = f"{case.get('classname', '')}::{case.get('name', '')}"
        outcome = "passed"
        for child in case: # Change so it doesn't stop at failing so we know how many passed, failed, errored?
            if child.tag in ("failure", "error"):
                outcome = "failed"
                break
            if child.tag == "skipped":
                outcome = "skipped"
        results[test_id] = outcome
    return results


def run_tests(ex: Executor, cfg: RepoConfig, task: Task, label: str, timeout: int = 1800) -> TestRun:
    candidates = runnable_test_files(task.test_files)
    if not candidates:
        return TestRun(rc=-1, note="no runnable test files")
    check = " ".join(shlex.quote(f) for f in candidates)
    present = ex.exec(f"for f in {check}; do [ -f \"$f\" ] && echo \"$f\"; done", user=AGENT_USER).out.split()
    if not present:
        return TestRun(rc=-1, note="test files missing after patch")
    junit = f"/tmp/ctx/{label}.xml"
    cmd = cfg.test_cmd.format(tests=" ".join(shlex.quote(f) for f in present), junit=junit)
    res = ex.exec(f"rm -f {junit}; {cmd}", user=AGENT_USER, timeout=timeout)
    xml_text = ex.exec(f"cat {junit} 2>/dev/null", user=AGENT_USER).out
    try:
        results = parse_junit(xml_text)
    except ET.ParseError as exc:
        return TestRun(rc=res.rc, timed_out=res.timed_out, note=f"bad junit: {exc}")
    note = "" if results else (res.err or res.out)[-1500:]
    return TestRun(rc=res.rc, results=results, timed_out=res.timed_out, note=note)


def score_after_agent(ex: Executor, cfg: RepoConfig, task: Task) -> dict:
    reset_test_files(ex, task)
    apply_patch(ex, task.test_patch, "test")
    run = run_tests(ex, cfg, task, "score")
    f2p_ok = [t for t in task.f2p if run.results.get(t) == "passed"]
    p2p_broken = [t for t in task.p2p if run.results.get(t) != "passed"]
    return {
        "passed": bool(task.f2p) and len(f2p_ok) == len(task.f2p) and not p2p_broken,
        "f2p_passed": len(f2p_ok),
        "f2p_total": len(task.f2p),
        "p2p_broken": p2p_broken[:20],
        "tests_timed_out": run.timed_out,
        "note": run.note[-500:],
    }
