"""The Task record: one merged change turned into a reproducible coding task."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Task:
    id: str                      # e.g. "networkx-8931"
    repo: str                    # repos.toml name
    sha: str                     # the merged commit (the human solution)
    base_sha: str                # its parent: the repo the agent starts from
    pr_number: int | None
    title: str
    prompt: str                  # what the agent is asked to do
    prompt_source: str           # "issue", "pr" or "commit": where the prompt came from
    test_files: list[str]        # test files the change touched (hidden from the agent)
    src_files: list[str]
    changed_lines: int
    test_patch: str              # applied only at scoring time
    gold_patch: str              # the human fix; used only to validate the task
    f2p: list[str] = field(default_factory=list)   # tests that fail before and pass after
    p2p: list[str] = field(default_factory=list)   # tests that pass both before and after
    validated: bool = False
    validation_note: str = ""

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.id}.json"
        path.write_text(json.dumps(asdict(self), indent=2))
        return path

    @classmethod
    def load(cls, path: Path) -> "Task":
        return cls(**json.loads(path.read_text()))


def task_dir(data_dir: Path, repo: str) -> Path:
    return data_dir / "tasks" / repo


def load_tasks(data_dir: Path, repo: str, validated_only: bool = False) -> list[Task]:
    tasks = [Task.load(p) for p in sorted(task_dir(data_dir, repo).glob("*.json"))]
    return [t for t in tasks if t.validated] if validated_only else tasks
