"""Command line: mine -> build -> validate -> run -> analyze.

    ctxstudy preflight networkx
    ctxstudy mine networkx --limit 30
    ctxstudy build networkx
    ctxstudy validate networkx
    ctxstudy run networkx --name pilot --tasks 8
    ctxstudy analyze pilot
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from ctxstudy import gitutil
from ctxstudy.analyze import load_trials, report
from ctxstudy.config import RepoConfig, StudyConfig, load_repos, load_study
from ctxstudy.executor import DockerExecutor, LocalExecutor, image_tag
from ctxstudy.mine import github_token_from_env, mine
from ctxstudy.run import run_all
from ctxstudy.tasks import load_tasks, task_dir
from ctxstudy.validate import validate_task


def _repo(name: str) -> RepoConfig:
    repos = load_repos()
    if name not in repos:
        sys.exit(f"unknown repo '{name}'. Known: {', '.join(repos)}")
    return repos[name]


def _clone_dir(study: StudyConfig, cfg: RepoConfig) -> Path:
    return study.data_dir / "clones" / cfg.name


def cmd_mine(args) -> int:
    cfg, study = _repo(args.repo), load_study()
    token = github_token_from_env()
    print(f"Mining {cfg.owner_repo} since {args.since or cfg.since} "
          f"({'with' if token else 'without'} GITHUB_TOKEN; without it, prompts come from commit messages)")
    tasks = mine(cfg, _clone_dir(study, cfg), since=args.since, limit=args.limit, github_token=token)
    out = task_dir(study.data_dir, cfg.name)
    for t in tasks:
        existing = out / f"{t.id}.json"
        if existing.exists() and not args.overwrite:
            continue  # keep validation results from earlier runs
        t.save(out)
    print(f"{len(tasks)} candidate tasks in {out}")
    return 0


def cmd_build(args) -> int:
    cfg, study = _repo(args.repo), load_study()
    if not cfg.install:
        sys.exit(f"Set `install` for {cfg.name} in repos.toml first.")
    sha = cfg.sha or gitutil.resolve_remote_head(cfg.url)
    if not cfg.sha:
        print(f"Note: sha not pinned; building {sha[:12]} (current HEAD). Pin it in repos.toml after validation.")
    clone_since = (date.fromisoformat(cfg.since) - timedelta(days=60)).isoformat()
    cmd = ["docker", "build", "-f", "docker/Dockerfile", "-t", image_tag(cfg),
           "--build-arg", f"PY_VERSION={cfg.python}",
           "--build-arg", f"REPO_URL={cfg.url}",
           "--build-arg", f"REPO_SHA={sha}",
           "--build-arg", f"CLONE_SINCE={clone_since}",
           "--build-arg", f"INSTALL_CMD={cfg.install}",
           "--build-arg", f"CLAUDE_CODE_VERSION={study.agent_version}"]
    if args.no_cache:
        cmd.append("--no-cache")
    cmd.append("docker")
    print(" ".join(cmd))
    return subprocess.call(cmd)


def cmd_validate(args) -> int:
    cfg, study = _repo(args.repo), load_study()
    tasks = load_tasks(study.data_dir, cfg.name)
    if args.task:
        tasks = [t for t in tasks if t.id in args.task]
    if not args.revalidate:
        tasks = [t for t in tasks if not t.validation_note]
    tasks = tasks[: args.limit] if args.limit else tasks
    print(f"Validating {len(tasks)} tasks with the {args.executor} executor")
    out = task_dir(study.data_dir, cfg.name)
    for t in tasks:
        if args.executor == "local":
            if not args.python:
                sys.exit("--python is required with --executor local (a venv with the repo's dependencies)")
            ex = LocalExecutor(cfg, _clone_dir(study, cfg), args.python)
        else:
            ex = DockerExecutor(cfg, study)
        try:
            with ex:
                validate_task(ex, cfg, t, flaky_check=not args.no_flaky_check)
        except Exception as exc:
            t.validated, t.validation_note = False, f"error: {exc}"[:500]
        t.save(out)
        print(f"  {'OK  ' if t.validated else 'SKIP'} {t.id}: {t.validation_note}")
    valid = load_tasks(study.data_dir, cfg.name, validated_only=True)
    print(f"{len(valid)} validated tasks for {cfg.name}")
    return 0


def cmd_run(args) -> int:
    cfg, study = _repo(args.repo), load_study()
    if args.attempts:
        study.attempts = args.attempts
    tasks = load_tasks(study.data_dir, cfg.name, validated_only=True)
    if not tasks:
        sys.exit("No validated tasks. Run `ctxstudy mine` and `ctxstudy validate` first.")
    if args.tasks and args.tasks < len(tasks):
        tasks = sorted(random.Random(study.seed).sample(tasks, args.tasks), key=lambda t: t.id)
    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") and not args.dry_run:
        sys.exit("CLAUDE_CODE_OAUTH_TOKEN is not set. Run `claude setup-token` and export it (see README).")
    run_dir = study.data_dir / "runs" / args.name
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = run_dir / "manifest.json"
    if not manifest.exists():
        manifest.write_text(json.dumps({
            "repo": cfg.name, "image": image_tag(cfg), "tasks": [t.id for t in tasks],
            "arms": study.arms, "attempts": study.attempts, "seed": study.seed,
            "model": study.model, "claude_code_version": study.agent_version,
            "max_turns": study.max_turns, "timeout_minutes": study.timeout_minutes,
            "egress": study.egress,
        }, indent=2))
    run_all(cfg, study, tasks, run_dir, dry_run=args.dry_run, max_trials=args.max_trials,
            pause_seconds=args.pause)
    return 0


def cmd_analyze(args) -> int:
    study = load_study()
    runs = args.runs or [p.name for p in (study.data_dir / "runs").iterdir() if p.is_dir()]
    trials, excluded = load_trials([study.data_dir / "runs" / r for r in runs])
    if not trials:
        sys.exit("No usable trials yet.")
    text = report(trials, excluded, title=f"Results: {', '.join(runs)}")
    out = study.data_dir / "reports" / f"{'+'.join(runs)}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(text)
    print(f"\nSaved to {out}")
    return 0


def cmd_preflight(args) -> int:
    cfg, study = _repo(args.repo), load_study()
    ok = True

    def check(label: str, passed: bool, hint: str = "") -> None:
        nonlocal ok
        ok &= passed
        print(f"  [{'ok' if passed else '!!'}] {label}" + ("" if passed else f"  -> {hint}"))

    print(f"Preflight for {cfg.name}")
    check("docker is available", shutil.which("docker") is not None and
          subprocess.run(["docker", "info"], capture_output=True).returncode == 0,
          "install Docker Desktop (or OrbStack) and start it")
    check("CLAUDE_CODE_OAUTH_TOKEN is set (value not shown)", bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")),
          "run `claude setup-token` on your Mac, then export CLAUDE_CODE_OAUTH_TOKEN=...")
    check("ANTHROPIC_API_KEY is not set (it would override your subscription)",
          not os.environ.get("ANTHROPIC_API_KEY"), "unset ANTHROPIC_API_KEY in this shell")
    check("GITHUB_TOKEN is set (optional: better task prompts)", bool(os.environ.get("GITHUB_TOKEN")),
          "optional; create a read-only fine-grained token for public repos")
    check(f"repos.toml has an install command for {cfg.name}", bool(cfg.install), "fill `install`")
    img = image_tag(cfg)
    has_image = subprocess.run(["docker", "image", "inspect", img], capture_output=True).returncode == 0
    check(f"image {img} exists", has_image, f"run `ctxstudy build {cfg.name}`")
    if has_image:
        ver = subprocess.run(["docker", "run", "--rm", "-e", "EGRESS=open", img, "cat",
                              "/home/agent/claude-version.txt"], capture_output=True, text=True)
        print(f"  Claude Code in image: {ver.stdout.strip() or ver.stderr.strip()}")
    valid = load_tasks(study.data_dir, cfg.name, validated_only=True)
    print(f"  {len(valid)} validated tasks")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ctxstudy", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("preflight", help="check Docker, auth and the repo image")
    s.add_argument("repo")
    s.set_defaults(fn=cmd_preflight)

    s = sub.add_parser("mine", help="turn merged PRs into candidate tasks")
    s.add_argument("repo")
    s.add_argument("--since", help="YYYY-MM-DD (default: repos.toml)")
    s.add_argument("--limit", type=int, default=40)
    s.add_argument("--overwrite", action="store_true", help="replace existing task files")
    s.set_defaults(fn=cmd_mine)

    s = sub.add_parser("build", help="build the repo's Docker image")
    s.add_argument("repo")
    s.add_argument("--no-cache", action="store_true")
    s.set_defaults(fn=cmd_build)

    s = sub.add_parser("validate", help="keep tasks whose tests fail before and pass after the fix")
    s.add_argument("repo")
    s.add_argument("--executor", choices=["docker", "local"], default="docker")
    s.add_argument("--python", help="python of a venv with the repo's deps (local executor)")
    s.add_argument("--limit", type=int)
    s.add_argument("--task", action="append", help="validate only this task id (repeatable)")
    s.add_argument("--revalidate", action="store_true", help="also re-check tasks validated before")
    s.add_argument("--no-flaky-check", action="store_true")
    s.set_defaults(fn=cmd_validate)

    s = sub.add_parser("run", help="run trials (with/without context files)")
    s.add_argument("repo")
    s.add_argument("--name", required=True, help="run name, e.g. pilot")
    s.add_argument("--tasks", type=int, help="sample this many validated tasks (seeded)")
    s.add_argument("--attempts", type=int, help="override attempts per arm")
    s.add_argument("--max-trials", type=int, help="stop after this many trials (resume later)")
    s.add_argument("--pause", type=int, default=0, help="seconds to wait between trials")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("analyze", help="write the results report")
    s.add_argument("runs", nargs="*", help="run names (default: all)")
    s.set_defaults(fn=cmd_analyze)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
