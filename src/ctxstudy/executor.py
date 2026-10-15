"""Where commands run: a fresh Docker container per trial, or a local temp dir.

DockerExecutor is the real thing. Every trial gets a new container from the
repo's image, so runs cannot contaminate each other. The container starts as
root only to install the egress firewall; the agent and the tests then run as
the unprivileged `agent` user (Claude Code refuses to bypass permissions as root).

LocalExecutor exists so you can validate tasks without Docker (for example on a
laptop while the image builds). It never runs the agent.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from ctxstudy.config import RepoConfig, StudyConfig
from ctxstudy.tasks import Task

AGENT_USER = "agent"
REPO_PATH_IN_CONTAINER = "/work/repo"
# Passed through from the host environment by name, so the value never appears
# in a command line, a log or this repo.
PASSTHROUGH_ENV = ["CLAUDE_CODE_OAUTH_TOKEN"]


@dataclass
class ExecResult:
    rc: int
    out: str
    err: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.rc == 0 and not self.timed_out


class Executor(ABC):
    repo_path: str

    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def materialize(self, task: Task) -> None:
        """Put the repo at task.base_sha into repo_path."""

    @abstractmethod
    def exec(self, cmd: str, user: str | None = None, timeout: int | None = None,
             env: dict[str, str] | None = None) -> ExecResult: ...

    @abstractmethod
    def write_file(self, path: str, content: str, user: str | None = None) -> None: ...

    @abstractmethod
    def copy_out(self, src: str, dest: Path) -> bool: ...

    @abstractmethod
    def stop(self) -> None: ...

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()


def image_tag(cfg: RepoConfig) -> str:
    return f"ctxstudy/{cfg.name}:{(cfg.sha or 'head')[:12]}"


class DockerExecutor(Executor):
    repo_path = REPO_PATH_IN_CONTAINER

    def __init__(self, cfg: RepoConfig, study: StudyConfig, egress: str | None = None):
        self.cfg, self.study = cfg, study
        self.image = image_tag(cfg)
        self.name = f"ctxstudy-{cfg.name}-{uuid.uuid4().hex[:8]}"
        self.egress = egress or study.egress

    def start(self) -> None:
        cmd = ["docker", "run", "-d", "--rm", "--name", self.name,
               "--cpus", str(self.study.cpus), "--memory", self.study.memory,
               "--cap-add", "NET_ADMIN", "--cap-add", "NET_RAW",
               "-e", f"EGRESS={self.egress}"]
        for var in PASSTHROUGH_ENV:
            cmd += ["-e", var]  # no '=value': docker copies it from our environment
        cmd.append(self.image)
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"docker run failed: {proc.stderr.strip()}")
        # Wait for the entrypoint to finish installing the firewall.
        for _ in range(60):
            ready = subprocess.run(["docker", "exec", self.name, "test", "-f", "/tmp/ready"],
                                   capture_output=True)
            if ready.returncode == 0:
                return
            state = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", self.name],
                                   capture_output=True, text=True)
            if state.stdout.strip() != "true":
                logs = subprocess.run(["docker", "logs", self.name], capture_output=True, text=True)
                raise RuntimeError(f"container exited during startup:\n{logs.stdout}{logs.stderr}")
            time.sleep(1)
        raise RuntimeError("container did not become ready within 60s")

    def materialize(self, task: Task) -> None:
        res = self.exec(
            f"git cat-file -e {task.base_sha}^{{commit}} && git checkout -qf {task.base_sha} && git clean -fdq",
            user=AGENT_USER)
        if not res.ok:
            raise RuntimeError(f"cannot check out {task.base_sha} in the image "
                               f"(rebuild with an earlier clone window?): {res.err.strip()}")

    def exec(self, cmd, user=None, timeout=None, env=None) -> ExecResult:
        args = ["docker", "exec", "-w", self.repo_path]
        if user:
            args += ["-u", user]
        for k, v in (env or {}).items():
            args += ["-e", f"{k}={v}"]
        args.append(self.name)
        if timeout:
            args += ["timeout", "--kill-after=15", str(timeout)]
        args += ["bash", "-c", cmd]
        try:
            proc = subprocess.run(args, capture_output=True, text=True,
                                  timeout=(timeout + 60) if timeout else None)
        except subprocess.TimeoutExpired as exc:
            return ExecResult(124, exc.stdout or "", exc.stderr or "", timed_out=True)
        return ExecResult(proc.returncode, proc.stdout, proc.stderr,
                          timed_out=bool(timeout) and proc.returncode in (124, 137))

    def write_file(self, path, content, user=None) -> None:
        args = ["docker", "exec", "-i"]
        if user:
            args += ["-u", user]
        args += [self.name, "bash", "-c", f"mkdir -p {shlex.quote(str(Path(path).parent))} && cat > {shlex.quote(path)}"]
        proc = subprocess.run(args, input=content, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"write {path} failed: {proc.stderr.strip()}")

    def copy_out(self, src, dest) -> bool:
        dest.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(["docker", "cp", f"{self.name}:{src}", str(dest)], capture_output=True)
        return proc.returncode == 0

    def stop(self) -> None:
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)


class LocalExecutor(Executor):
    """Validation without Docker: a temp dir per task and a local virtualenv.

    Point --python at a venv that has the repo's dependencies installed. The
    snapshot is imported from the working directory (python -m pytest adds it
    to sys.path), so no editable install is needed for flat-layout repos.
    """

    def __init__(self, cfg: RepoConfig, clone_dir: Path, python: str):
        self.cfg, self.clone_dir, self.python = cfg, clone_dir, python
        self.root: Path | None = None
        self.repo_path = ""

    def start(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix=f"ctxstudy-{self.cfg.name}-"))
        self.repo_path = str(self.root / "repo")
        Path(self.repo_path).mkdir()
        (self.root / "ctx").mkdir()

    def materialize(self, task: Task) -> None:
        archive = subprocess.run(["git", "-C", str(self.clone_dir), "archive", task.base_sha],
                                 capture_output=True)
        if archive.returncode != 0:
            raise RuntimeError(archive.stderr.decode())
        subprocess.run(["tar", "-x", "-C", self.repo_path], input=archive.stdout, check=True)

    def exec(self, cmd, user=None, timeout=None, env=None) -> ExecResult:
        bindir = str(Path(self.python).parent)
        full_env = {**os.environ, **(env or {}),
                    "PATH": f"{bindir}:{os.environ.get('PATH', '')}"}
        if self.cfg.pythonpath:
            full_env["PYTHONPATH"] = str(Path(self.repo_path) / self.cfg.pythonpath)
        # Map container paths used by shared steps onto this temp dir.
        cmd = cmd.replace("/tmp/ctx", str(self.root / "ctx"))
        try:
            proc = subprocess.run(["bash", "-c", cmd], cwd=self.repo_path, env=full_env,
                                  capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            return ExecResult(124, str(exc.stdout or ""), str(exc.stderr or ""), timed_out=True)
        return ExecResult(proc.returncode, proc.stdout, proc.stderr)

    def write_file(self, path, content, user=None) -> None:
        path = path.replace("/tmp/ctx", str(self.root / "ctx"))
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(content)

    def copy_out(self, src, dest) -> bool:
        src = src.replace("/tmp/ctx", str(self.root / "ctx"))
        if not Path(src).exists():
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dest)
        return True

    def stop(self) -> None:
        if self.root:
            shutil.rmtree(self.root, ignore_errors=True)
