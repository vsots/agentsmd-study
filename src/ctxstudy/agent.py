"""Run Claude Code headless inside the trial container and read its transcript.

Choices, each from the Claude Code docs (see README "Sources"):
  - -p with --output-format stream-json --verbose: the full event log; its last
    line is the result with turns, duration, token usage and an estimated cost.
  - --permission-mode bypassPermissions: no prompts. Safe only because the
    container is disposable, runs as a non-root user and can only reach Anthropic.
    We pass the mode explicitly because disabling nonessential traffic also
    disables the feature-flag fetch that picks the default mode.
  - Subscription auth via CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`).
    We unset ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN because they would take
    precedence. Never use --bare: it ignores subscription auth.
  - A fresh CLAUDE_CONFIG_DIR, auto memory off and an empty strict MCP config,
    so nothing from a user profile or a previous run leaks into the trial.
  - CLAUDE_CODE_RETRY_WATCHDOG=1: retry on rate limits and wait for the reset
    window, which matters on a Pro/Max plan.
"""

from __future__ import annotations

import json
import shlex
import time
from collections import Counter

from ctxstudy.config import StudyConfig
from ctxstudy.executor import AGENT_USER, Executor

CTX = "/tmp/ctx"
AGENT_ENV = {
    "CLAUDE_CONFIG_DIR": f"{CTX}/claude-config",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "DISABLE_AUTOUPDATER": "1",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "CLAUDE_CODE_RETRY_WATCHDOG": "1",
}
TEST_RUNNER_HINTS = ("pytest", "unittest", "runtests", "tox", "nox", "make test")


def build_command(study: StudyConfig) -> str:
    env = " ".join(f"{k}={shlex.quote(v)}" for k, v in AGENT_ENV.items())
    flags = [
        "-p", f'"$(cat {CTX}/prompt.txt)"',
        "--output-format", "stream-json", "--verbose",
        "--permission-mode", "bypassPermissions",
        "--max-turns", str(study.max_turns),
        "--model", shlex.quote(study.model),
        "--no-session-persistence",
        "--strict-mcp-config", "--mcp-config", f"{CTX}/mcp.json",
    ]
    return (f"mkdir -p {CTX}/claude-config && "
            f"env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN {env} "
            f"claude {' '.join(flags)} > {CTX}/transcript.jsonl 2> {CTX}/agent.stderr")


def parse_transcript(text: str) -> dict:
    events = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), {})
    result = next((e for e in reversed(events) if e.get("type") == "result"), {})

    tools: Counter[str] = Counter()
    bash_cmds: list[str] = []
    for e in events:
        if e.get("type") != "assistant":
            continue
        for block in (e.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tools[block.get("name", "?")] += 1
                if block.get("name") == "Bash":
                    bash_cmds.append(str((block.get("input") or {}).get("command", ""))[:300])

    usage = result.get("usage") or {}
    return {
        "model": init.get("model"),
        "claude_code_version": init.get("claude_code_version"),
        "permission_mode": init.get("permissionMode"),
        "api_key_source": init.get("apiKeySource"),
        "subtype": result.get("subtype"),
        "is_error": result.get("is_error"),
        "num_turns": result.get("num_turns"),
        "duration_ms": result.get("duration_ms"),
        "duration_api_ms": result.get("duration_api_ms"),
        "total_cost_usd": result.get("total_cost_usd"),  # client-side estimate, not billing
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "model_usage": result.get("modelUsage") or result.get("model_usage"),
        "tool_counts": dict(tools),
        "bash_commands": len(bash_cmds),
        "ran_tests": any(h in c for c in bash_cmds for h in TEST_RUNNER_HINTS),
        "result_text": str(result.get("result") or "")[:2000],
        "events": len(events),
    }


def run_agent(ex: Executor, study: StudyConfig, prompt: str) -> tuple[dict, str]:
    ex.write_file(f"{CTX}/prompt.txt", prompt, user=AGENT_USER)
    ex.write_file(f"{CTX}/mcp.json", json.dumps({"mcpServers": {}}), user=AGENT_USER)
    started = time.monotonic()
    res = ex.exec(build_command(study), user=AGENT_USER, timeout=study.timeout_minutes * 60)
    wall = time.monotonic() - started
    transcript = ex.exec(f"cat {CTX}/transcript.jsonl 2>/dev/null").out
    stderr_tail = ex.exec(f"tail -c 3000 {CTX}/agent.stderr 2>/dev/null").out
    metrics = parse_transcript(transcript)
    metrics.update(wall_seconds=round(wall, 1), exit_code=res.rc, timed_out=res.timed_out,
                   stderr_tail=stderr_tail)
    return metrics, transcript


def looks_like_auth_failure(metrics: dict) -> bool:
    text = f"{metrics.get('result_text', '')} {metrics.get('stderr_tail', '')}".lower()
    return any(s in text for s in ("invalid api key", "authentication", "not logged in",
                                   "oauth", "401", "please run /login"))
