"""Prepare the repo for one trial: base commit, treatment arm, clean history.

The treatment is "the instruction files Claude Code loads as memory":
AGENTS.md, CLAUDE.md and CLAUDE.local.md at any depth, plus .claude/rules/.

  with    -> repo as-is. If it has AGENTS.md but no CLAUDE.md, we add a one-line
             CLAUDE.md containing "@AGENTS.md", the documented way to make sure
             Claude Code loads it whatever its version or telemetry settings.
  without -> all of those files removed.

In BOTH arms we remove project settings and MCP config (.claude/settings*.json,
.mcp.json), so hooks and MCP servers can't differ between arms or run code
we didn't intend. Then we replace .git with a single fresh commit, so the agent
can't read the future fix from history or notice deleted files in git status.
"""

from __future__ import annotations

import shlex

from ctxstudy.executor import AGENT_USER, Executor
from ctxstudy.tasks import Task

# A find expression without an action; callers add -print or -print0.
CONTEXT_EXPR = (
    "find . -path ./.git -prune -o \\( -name AGENTS.md -o -name CLAUDE.md "
    "-o -name CLAUDE.local.md \\)"
)
ALWAYS_REMOVE = [".claude/settings.json", ".claude/settings.local.json", ".mcp.json"]

# Returns small shell script that runs inside trial sandbox to set up one treatment arm.
def arm_script(arm: str) -> str:
    common = "rm -f " + " ".join(ALWAYS_REMOVE)
    if arm == "without":
        return (f"{common}\n"
                f"{CONTEXT_EXPR} -print0 | xargs -0 -r rm -f\n"
                "rm -rf .claude/rules\n")
    if arm == "with":
        return (f"{common}\n"
                "if [ -e AGENTS.md ] && [ ! -e CLAUDE.md ]; then printf '@AGENTS.md\\n' > CLAUDE.md; fi\n")
    raise ValueError(f"unknown arm: {arm}")


FRESH_HISTORY = ( # Replaces git history with single fresh commit
    "rm -rf .git && git init -q && git add -A && "
    "git -c user.name=ctxstudy -c user.email=ctxstudy@localhost commit -q --no-verify -m 'Task snapshot'"
)


def context_inventory(ex: Executor) -> list[dict]:
    """Which context files exist right now, with sizes (recorded per trial)."""
    res = ex.exec(f"{CONTEXT_EXPR} -print | while read -r f; do printf '%s\\t%s\\n' \"$f\" \"$(wc -c < \"$f\")\"; done",
                  user=AGENT_USER)
    files = []
    for line in res.out.splitlines():
        if "\t" in line:
            path, size = line.split("\t", 1)
            files.append({"path": path.removeprefix("./"), "bytes": int(size.strip() or 0)})
    return files


def prepare(ex: Executor, task: Task, arm: str) -> dict:
    """Base commit -> arm -> fresh history. Returns what the arm saw."""
    ex.materialize(task)
    before = context_inventory(ex)
    res = ex.exec(arm_script(arm), user=AGENT_USER)
    if not res.ok:
        raise RuntimeError(f"arm setup failed: {res.err}")
    after = context_inventory(ex)
    res = ex.exec(FRESH_HISTORY, user=AGENT_USER)
    if not res.ok:
        raise RuntimeError(f"fresh history failed: {res.err}")
    return {"context_files_in_repo": before, "context_files_in_arm": after}


def runnable_test_files(paths: list[str]) -> list[str]:
    """Test modules we can hand to pytest (not conftest.py, data files, etc.)."""
    out = []
    for p in paths:
        name = p.rsplit("/", 1)[-1]
        if not p.endswith(".py") or name in ("conftest.py", "__init__.py"):
            continue
        if name.startswith("test_") or name.endswith("_test.py"):
            out.append(p)
    return out


def apply_patch(ex: Executor, patch: str, label: str) -> None:
    if not patch.strip():
        return
    path = f"/tmp/ctx/{label}.patch"
    ex.write_file(path, patch, user=AGENT_USER)
    res = ex.exec(f"git apply --whitespace=nowarn {path}", user=AGENT_USER)
    if not res.ok:
        raise RuntimeError(f"{label} patch did not apply: {res.err.strip()}")


def reset_test_files(ex: Executor, task: Task) -> None:
    """Undo any agent edits to the hidden test files before applying the real ones."""
    script = []
    for f in task.test_files:
        q = shlex.quote(f)
        script.append(f"if git cat-file -e HEAD:{q} 2>/dev/null; then git checkout -q HEAD -- {q}; else rm -f {q}; fi")
    if script:
        res = ex.exec("\n".join(script), user=AGENT_USER)
        if not res.ok:
            raise RuntimeError(f"resetting test files failed: {res.err}")
