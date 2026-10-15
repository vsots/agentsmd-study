"""Turn a repo's merged history into candidate tasks.

Recipe (the SWE-bench idea, applied to one repo):
  1. Walk squash-merged commits on the default branch ("Fix X (#123)").
  2. Keep changes that touched both source and tests, and are small enough to judge.
  3. Split the diff: the test part is hidden and applied only when scoring;
     the source part (the human fix) is used only to validate the task.
  4. Write the task statement from the linked issue, else the PR, else the commit.

Validation (validate.py) later keeps only tasks whose tests fail before the fix
and pass after it.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path

from ctxstudy import gitutil
from ctxstudy.config import RepoConfig
from ctxstudy.tasks import Task

PR_RE = re.compile(r"\(#(\d+)\)\s*$")
CLOSES_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.IGNORECASE)
BOT_MARKERS = ("[bot]", "dependabot", "pre-commit-ci", "renovate")
SKIP_SUBJECT_PREFIXES = ("bump ", "[pre-commit.ci]", "release ", "revert ")


# ---------------------------------------------------------------- path classes

def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Glob with ** support: '**/' = any number of directories, '*' = within one segment."""
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(f"^{out}$")


def matches_any(path: str, patterns: list[str]) -> bool:
    return any(glob_to_regex(p).match(path) for p in patterns)


def classify(path: str, cfg: RepoConfig) -> str:
    if matches_any(path, cfg.test_globs):
        return "test"
    if matches_any(path, cfg.exclude_globs):
        return "other"
    if matches_any(path, cfg.src_globs):
        return "src"
    return "other"


# ---------------------------------------------------------------- prompts

def clean_markdown(text: str) -> str:
    """Strip PR-template noise: HTML comments, checklists, trailers."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    keep = []
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^- \[[ xX]\]", stripped):
            continue
        if re.match(r"^(Co-authored-by|Signed-off-by):", stripped, re.IGNORECASE):
            continue
        keep.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(keep)).strip()


def prompt_from_commit(subject: str, body: str) -> str:
    """Fallback when there is no GitHub token: the commit subject plus any prose
    in the body. Squash bodies are often just a list of '* commit' lines, which we drop."""
    title = PR_RE.sub("", subject).strip()
    prose = [l for l in body.splitlines() if l.strip() and not l.lstrip().startswith("* ")]
    text = title + ("\n\n" + "\n".join(prose) if prose else "")
    return clean_markdown(text)


def _github_get(path: str, token: str) -> dict:
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def prompt_from_github(owner_repo: str, pr_number: int, token: str) -> tuple[str, str]:
    """Prefer the linked issue (what a developer would actually be handed),
    else the PR description. Returns (prompt, source)."""
    pr = _github_get(f"/repos/{owner_repo}/pulls/{pr_number}", token)
    body = pr.get("body") or ""
    issue_ref = CLOSES_RE.search(body)
    if issue_ref:
        try:
            issue = _github_get(f"/repos/{owner_repo}/issues/{issue_ref.group(1)}", token)
            text = clean_markdown(f"{issue['title']}\n\n{issue.get('body') or ''}")
            if len(text) > 40:
                return text, "issue"
        except Exception:
            pass
    return clean_markdown(f"{pr['title']}\n\n{body}"), "pr"


# ---------------------------------------------------------------- mining

def mine(cfg: RepoConfig, clone_dir: Path, since: str | None = None, limit: int = 50,
         github_token: str | None = None, log=print) -> list[Task]:
    since = since or cfg.since
    gitutil.clone_or_fetch(cfg.url, clone_dir, since)
    tasks: list[Task] = []
    for c in gitutil.first_parent_commits(clone_dir, since):
        if len(tasks) >= limit:
            break
        pr = PR_RE.search(c.subject)
        if not pr:
            continue
        if any(m in c.author.lower() for m in BOT_MARKERS):
            continue
        if c.subject.lower().startswith(SKIP_SUBJECT_PREFIXES):
            continue
        if not gitutil.has_commit(clone_dir, c.parent):
            continue  # the parent sits outside the shallow window

        files = gitutil.changed_files(clone_dir, c.parent, c.sha)
        tests = [f for f in files if classify(f, cfg) == "test"]
        srcs = [f for f in files if classify(f, cfg) == "src"]
        if not tests or not srcs or len(srcs) > cfg.max_src_files:
            continue
        lines = gitutil.changed_line_count(clone_dir, c.parent, c.sha)
        if lines > cfg.max_changed_lines:
            continue

        pr_number = int(pr.group(1))
        prompt, source = prompt_from_commit(c.subject, c.body), "commit"
        if github_token:
            try:
                prompt, source = prompt_from_github(cfg.owner_repo, pr_number, github_token)
            except Exception as exc:  # fall back rather than lose the task
                log(f"  GitHub lookup failed for #{pr_number}: {exc}")

        non_tests = [f for f in files if f not in tests]
        task = Task(
            id=f"{cfg.name}-{pr_number}",
            repo=cfg.name,
            sha=c.sha,
            base_sha=c.parent,
            pr_number=pr_number,
            title=PR_RE.sub("", c.subject).strip(),
            prompt=prompt,
            prompt_source=source,
            test_files=tests,
            src_files=srcs,
            changed_lines=lines,
            test_patch=gitutil.diff(clone_dir, c.parent, c.sha, tests),
            gold_patch=gitutil.diff(clone_dir, c.parent, c.sha, non_tests),
        )
        tasks.append(task)
        log(f"  + {task.id}: {task.title[:70]} ({len(srcs)} src, {len(tests)} test files, {lines} lines)")
    return tasks


def github_token_from_env() -> str | None:
    return os.environ.get("GITHUB_TOKEN") or None
