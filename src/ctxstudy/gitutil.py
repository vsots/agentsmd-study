"""Thin wrappers around the git CLI.

We shell out to git instead of using a library: it is what every repo already
uses, it handles partial (blobless) clones, and the commands are easy to audit.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    pass


def git(args: list[str], cwd: Path | None = None, input: str | None = None) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, input=input, capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


@dataclass
class Commit:
    sha: str
    parent: str
    author: str
    date: str
    subject: str
    body: str


def clone_or_fetch(url: str, dest: Path, since: str) -> Path:
    """Blobless, shallow-since clone: full commit history for the window, file
    contents fetched only when we actually read them. Fast even for big repos."""
    if (dest / ".git").exists():
        git(["fetch", "--quiet", f"--shallow-since={since}", "origin"], cwd=dest)
        git(["reset", "--quiet", "--soft", "FETCH_HEAD"], cwd=dest)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    git(["clone", "--quiet", "--filter=blob:none", "--no-checkout",
         f"--shallow-since={since}", url, str(dest)])
    return dest


def first_parent_commits(repo: Path, since: str) -> list[Commit]:
    """Commits on the default branch's first-parent line, newest first.

    Squash-merged PRs show up here as single commits whose subject ends in (#123).
    """
    sep, end = "\x1f", "\x1e"
    fmt = sep.join(["%H", "%P", "%an", "%aI", "%s", "%b"]) + end
    out = git(["log", "--first-parent", f"--since={since}", f"--format={fmt}", "HEAD"], cwd=repo)
    commits = []
    for record in out.split(end):
        record = record.strip("\n")
        if not record:
            continue
        sha, parents, author, date, subject, body = record.split(sep, 5)
        parent = parents.split()[0] if parents else ""
        commits.append(Commit(sha, parent, author, date, subject, body.strip()))
    return commits


def has_commit(repo: Path, sha: str) -> bool:
    if not sha:
        return False
    try:
        git(["cat-file", "-e", f"{sha}^{{commit}}"], cwd=repo)
        return True
    except GitError:
        return False


def changed_files(repo: Path, base: str, head: str) -> list[str]:
    return [p for p in git(["diff", "--name-only", base, head], cwd=repo).splitlines() if p]


def changed_line_count(repo: Path, base: str, head: str) -> int:
    total = 0
    for line in git(["diff", "--numstat", base, head], cwd=repo).splitlines():
        added, removed, _ = line.split("\t", 2)
        if added != "-":  # binary files report '-'
            total += int(added) + int(removed)
    return total


def diff(repo: Path, base: str, head: str, paths: list[str]) -> str:
    if not paths:
        return ""
    return git(["diff", "--binary", base, head, "--", *paths], cwd=repo)


def resolve_remote_head(url: str) -> str:
    out = git(["ls-remote", url, "HEAD"])
    return out.split()[0]
