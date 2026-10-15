"""Keep only tasks with a clear, reproducible signal.

For each mined task, in the same sandbox the trials will use:
  1. base commit + hidden tests  -> record results ("before")
  2. + the human fix             -> record results ("after"), twice to catch flakes
A task is valid when at least one test fails before and passes after (fail-to-pass).
Tests that pass before and after (pass-to-pass) guard against collateral damage.
"""

from __future__ import annotations

from ctxstudy.config import RepoConfig
from ctxstudy.executor import AGENT_USER, Executor
from ctxstudy.score import run_tests
from ctxstudy.tasks import Task
from ctxstudy.workspace import apply_patch, prepare


def validate_task(ex: Executor, cfg: RepoConfig, task: Task, flaky_check: bool = True) -> Task:
    ex.exec("mkdir -p /tmp/ctx", user=AGENT_USER)
    prepare(ex, task, "with")
    apply_patch(ex, task.test_patch, "test")
    before = run_tests(ex, cfg, task, "before")
    apply_patch(ex, task.gold_patch, "gold")
    after = run_tests(ex, cfg, task, "after")
    again = run_tests(ex, cfg, task, "after2") if flaky_check else after

    def stable_pass(test_id: str) -> bool:
        return after.results.get(test_id) == "passed" and again.results.get(test_id) == "passed"

    task.f2p = sorted(t for t in after.results if stable_pass(t) and before.results.get(t) != "passed")
    task.p2p = sorted(t for t in after.results if stable_pass(t) and before.results.get(t) == "passed")
    flaky = sorted(t for t in after.results if after.results[t] != again.results.get(t))

    if not after.results:
        task.validated, task.validation_note = False, f"no test results after the fix: {after.note[-300:]}"
    elif not task.f2p:
        task.validated, task.validation_note = False, "no fail-to-pass tests"
    else:
        task.validated = True
        task.validation_note = f"{len(task.f2p)} fail-to-pass, {len(task.p2p)} pass-to-pass"
        if flaky:
            task.validation_note += f", {len(flaky)} flaky tests excluded"
    return task
