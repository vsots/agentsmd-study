"""Plan and run trials: task x arm x attempt, one fresh container each.

The trial order is shuffled with a fixed seed. Interleaving arms this way means
slow drift (rate limits, time of day, a model update) hits both arms equally
instead of biasing whichever arm happened to run last.

Results append to data/runs/<name>/trials.jsonl, one JSON object per trial, so
a run can be stopped and resumed: finished trial IDs are skipped.
"""

from __future__ import annotations

import json
import random
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ctxstudy.agent import looks_like_auth_failure, run_agent
from ctxstudy.config import RepoConfig, StudyConfig
from ctxstudy.executor import AGENT_USER, DockerExecutor, Executor, image_tag
from ctxstudy.mine import classify
from ctxstudy.score import score_after_agent
from ctxstudy.tasks import Task
from ctxstudy.workspace import prepare

CONTEXT_NAMES = ("AGENTS.md", "CLAUDE.md", "CLAUDE.local.md")


@dataclass
class TrialSpec:
    task: Task
    arm: str
    attempt: int

    @property
    def trial_id(self) -> str:
        return f"{self.task.id}__{self.arm}__{self.attempt}"


def plan(tasks: list[Task], arms: list[str], attempts: int, seed: int) -> list[TrialSpec]:
    specs = [TrialSpec(t, arm, k) for t in tasks for k in range(attempts) for arm in arms]
    random.Random(seed).shuffle(specs)
    return specs


def change_summary(ex: Executor, cfg: RepoConfig) -> tuple[dict, str]:
    """What the agent changed, before we touch anything for scoring."""
    ex.exec("git add -A -N . >/dev/null 2>&1 || true", user=AGENT_USER)  # show new files in diffs
    numstat = ex.exec("git diff --numstat HEAD", user=AGENT_USER).out
    patch = ex.exec("git diff HEAD", user=AGENT_USER).out
    files, added, removed = [], 0, 0
    for line in numstat.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        a, r, path = parts
        files.append(path)
        if a != "-":
            added, removed = added + int(a), removed + int(r)
    return {
        "files_changed": len(files),
        "lines_added": added,
        "lines_removed": removed,
        "touched_tests": any(classify(f, cfg) == "test" for f in files),
        "touched_context_files": any(f.rsplit("/", 1)[-1] in CONTEXT_NAMES for f in files),
        "files": files[:50],
    }, patch


def run_trial(spec: TrialSpec, cfg: RepoConfig, study: StudyConfig, template: str,
              run_dir: Path) -> dict:
    record = {
        "trial_id": spec.trial_id, "repo": cfg.name, "task_id": spec.task.id,
        "arm": spec.arm, "attempt": spec.attempt, "image": image_tag(cfg),
        "model_requested": study.model, "started_at": datetime.now(timezone.utc).isoformat(),
        "score": None, "infra_error": None,
    }
    try:
        with DockerExecutor(cfg, study) as ex:
            ex.exec("mkdir -p /tmp/ctx", user=AGENT_USER)
            record["claude_code_version_image"] = ex.exec(
                "cat /home/agent/claude-version.txt 2>/dev/null").out.strip()
            record["workspace"] = prepare(ex, spec.task, spec.arm)
            prompt = template.replace("{prompt}", spec.task.prompt)
            metrics, transcript = run_agent(ex, study, prompt)
            record["agent"] = metrics
            (run_dir / "transcripts").mkdir(parents=True, exist_ok=True)
            (run_dir / "transcripts" / f"{spec.trial_id}.jsonl").write_text(transcript)
            changes, patch = change_summary(ex, cfg)
            record["changes"] = changes
            (run_dir / "diffs").mkdir(parents=True, exist_ok=True)
            (run_dir / "diffs" / f"{spec.trial_id}.patch").write_text(patch)
            record["score"] = score_after_agent(ex, cfg, spec.task)
    except Exception as exc:  # recorded and excluded from analysis, never silently dropped
        record["infra_error"] = f"{type(exc).__name__}: {exc}"
        record["traceback"] = traceback.format_exc()[-3000:]
    record["finished_at"] = datetime.now(timezone.utc).isoformat()
    return record


def run_all(cfg: RepoConfig, study: StudyConfig, tasks: list[Task], run_dir: Path,
            dry_run: bool = False, max_trials: int | None = None, pause_seconds: int = 0,
            log=print) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    results_path = run_dir / "trials.jsonl"
    done = set()
    if results_path.exists():
        for line in results_path.read_text().splitlines():
            rec = json.loads(line)
            if not rec.get("infra_error"):
                done.add(rec["trial_id"])
    template = study.prompt_template.read_text()
    specs = plan(tasks, study.arms, study.attempts, study.seed)
    todo = [s for s in specs if s.trial_id not in done]
    log(f"{len(specs)} trials planned, {len(done)} already done, {len(todo)} to run")
    if dry_run:
        for s in todo:
            log(f"  {s.trial_id}")
        return

    consecutive_failures = 0
    for i, spec in enumerate(todo[: max_trials or len(todo)], 1):
        log(f"[{i}/{len(todo)}] {spec.trial_id}")
        rec = run_trial(spec, cfg, study, template, run_dir)
        with results_path.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
        agent = rec.get("agent") or {}
        if rec["infra_error"]:
            consecutive_failures += 1
            log(f"    infra error: {rec['infra_error']}")
        else:
            consecutive_failures = 0
            log(f"    passed={rec['score']['passed']} turns={agent.get('num_turns')} "
                f"wall={agent.get('wall_seconds')}s est_cost=${agent.get('total_cost_usd')}")
        if (agent.get("is_error") or not agent.get("events")) and looks_like_auth_failure(agent):
            log("Stopping: this looks like an authentication problem. Check CLAUDE_CODE_OAUTH_TOKEN "
                "and that ANTHROPIC_API_KEY is not set (see README).")
            return
        if consecutive_failures >= 3:
            log("Stopping after 3 infrastructure errors in a row; see trials.jsonl for tracebacks.")
            return
        if pause_seconds:
            time.sleep(pause_seconds)
