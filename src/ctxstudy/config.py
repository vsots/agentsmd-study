"""Load study.toml and repos.toml.

Design note: every setting that can change a result lives in these two files,
so a published result can name the exact configuration that produced it.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path.cwd()


@dataclass
class RepoConfig:
    name: str
    url: str
    sha: str = ""
    install: str = ""
    status: str = "todo"
    notes: str = ""
    python: str = "3.12"
    since: str = "2025-10-01"
    max_src_files: int = 4
    max_changed_lines: int = 400
    test_globs: list[str] = field(default_factory=list)
    src_globs: list[str] = field(default_factory=list)
    exclude_globs: list[str] = field(default_factory=list)
    test_cmd: str = ""
    pythonpath: str = ""

    @property
    def owner_repo(self) -> str:
        """'https://github.com/networkx/networkx' -> 'networkx/networkx'."""
        return "/".join(self.url.rstrip("/").removesuffix(".git").split("/")[-2:])


@dataclass
class StudyConfig:
    agent_name: str
    agent_version: str
    model: str
    max_turns: int
    timeout_minutes: int
    arms: list[str]
    attempts: int
    seed: int
    cpus: int
    memory: str
    egress: str
    data_dir: Path
    prompt_template: Path


def load_repos(path: Path | None = None) -> dict[str, RepoConfig]:
    path = path or ROOT / "repos.toml"
    raw = tomllib.loads(path.read_text())
    defaults = raw.get("defaults", {})
    repos: dict[str, RepoConfig] = {}
    for entry in raw.get("repo", []):
        merged = {**defaults, **{k: v for k, v in entry.items() if v != "" or k not in defaults}}
        repos[entry["name"]] = RepoConfig(**merged)
    return repos


def load_study(path: Path | None = None) -> StudyConfig:
    path = path or ROOT / "study.toml"
    raw = tomllib.loads(path.read_text())
    agent, design, sandbox, paths = raw["agent"], raw["design"], raw["sandbox"], raw["paths"]
    return StudyConfig(
        agent_name=agent["name"],
        agent_version=str(agent["version"]),
        model=agent["model"],
        max_turns=int(agent["max_turns"]),
        timeout_minutes=int(agent["timeout_minutes"]),
        arms=list(design["arms"]),
        attempts=int(design["attempts"]),
        seed=int(design["seed"]),
        cpus=int(sandbox["cpus"]),
        memory=str(sandbox["memory"]),
        egress=str(sandbox["egress"]),
        data_dir=ROOT / paths["data"],
        prompt_template=ROOT / paths["prompt_template"],
    )
