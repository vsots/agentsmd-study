"""End-to-end check of the workspace steps on a throwaway git repo (no Docker)."""

import subprocess
import sys
from pathlib import Path

import pytest

from ctxstudy.config import RepoConfig
from ctxstudy.executor import LocalExecutor
from ctxstudy.tasks import Task
from ctxstudy.workspace import (apply_patch, context_inventory, prepare, reset_test_files,
                                runnable_test_files)


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str]:
    r = tmp_path / "src-repo"
    (r / "pkg" / "tests").mkdir(parents=True)
    (r / ".claude" / "rules").mkdir(parents=True)
    (r / "AGENTS.md").write_text("# Rules\nRun pytest.\n")
    (r / "pkg" / "CLAUDE.md").write_text("Nested instructions\n")
    (r / ".claude" / "rules" / "style.md").write_text("Use tabs\n")
    (r / ".claude" / "settings.json").write_text('{"hooks": {}}\n')
    (r / ".mcp.json").write_text('{"mcpServers": {}}\n')
    (r / "pkg" / "__init__.py").write_text("")
    (r / "pkg" / "core.py").write_text("def add(a, b):\n    return a - b\n")
    (r / "pkg" / "tests" / "test_core.py").write_text("from pkg.core import add\n\ndef test_add():\n    assert add(1, 1) == 2\n")
    _git(r, "init", "-q")
    _git(r, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    _git(r, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    return r, _git(r, "rev-parse", "HEAD").strip()


def _task(sha: str) -> Task:
    return Task(id="t-1", repo="x", sha=sha, base_sha=sha, pr_number=1, title="t", prompt="p",
                prompt_source="commit", test_files=["pkg/tests/test_core.py"], src_files=["pkg/core.py"],
                changed_lines=2, test_patch="", gold_patch="")


def _executor(repo_dir: Path) -> LocalExecutor:
    return LocalExecutor(RepoConfig(name="x", url="https://github.com/o/x"), repo_dir, sys.executable)


def test_without_arm_removes_all_context_and_resets_history(repo):
    repo_dir, sha = repo
    with _executor(repo_dir) as ex:
        seen = prepare(ex, _task(sha), "without")
        root = Path(ex.repo_path)
        assert {f["path"] for f in seen["context_files_in_repo"]} == {"AGENTS.md", "pkg/CLAUDE.md"}
        assert seen["context_files_in_arm"] == []
        assert not (root / ".claude" / "rules").exists()
        assert not (root / ".claude" / "settings.json").exists()
        assert not (root / ".mcp.json").exists()
        log = ex.exec("git log --oneline").out.strip().splitlines()
        assert len(log) == 1 and "Task snapshot" in log[0]
        assert ex.exec("git status --porcelain").out.strip() == ""


def test_with_arm_keeps_context_and_adds_claude_import(repo):
    repo_dir, sha = repo
    with _executor(repo_dir) as ex:
        prepare(ex, _task(sha), "with")
        root = Path(ex.repo_path)
        assert (root / "AGENTS.md").exists()
        assert (root / "CLAUDE.md").read_text() == "@AGENTS.md\n"
        assert (root / ".claude" / "rules" / "style.md").exists()
        assert not (root / ".claude" / "settings.json").exists()  # removed in both arms
        assert {f["path"] for f in context_inventory(ex)} == {"AGENTS.md", "CLAUDE.md", "pkg/CLAUDE.md"}


def test_reset_and_apply_test_patch(repo):
    repo_dir, sha = repo
    patch = (
        "diff --git a/pkg/tests/test_core.py b/pkg/tests/test_core.py\n"
        "--- a/pkg/tests/test_core.py\n"
        "+++ b/pkg/tests/test_core.py\n"
        "@@ -2,3 +2,6 @@ from pkg.core import add\n"
        " \n"
        " def test_add():\n"
        "     assert add(1, 1) == 2\n"
        "+\n"
        "+def test_add_negative():\n"
        "+    assert add(-1, -1) == -2\n"
    )
    with _executor(repo_dir) as ex:
        task = _task(sha)
        task.test_patch = patch
        prepare(ex, task, "with")
        test_file = Path(ex.repo_path) / "pkg/tests/test_core.py"
        test_file.write_text("# the agent rewrote this test\n")
        reset_test_files(ex, task)
        apply_patch(ex, task.test_patch, "test")
        assert "test_add_negative" in test_file.read_text()
        assert "agent rewrote" not in test_file.read_text()


def test_runnable_test_files():
    files = ["a/tests/test_x.py", "a/tests/conftest.py", "a/tests/data.json", "a/x_test.py", "a/tests/__init__.py"]
    assert runnable_test_files(files) == ["a/tests/test_x.py", "a/x_test.py"]
