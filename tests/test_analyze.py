import math

from ctxstudy.analyze import correctness, efficiency, report, tails
from ctxstudy.agent import build_command, parse_transcript
from ctxstudy.config import StudyConfig
from pathlib import Path


def _trial(task, arm, passed, wall, turns):
    return {"repo": "r", "task_id": task, "arm": arm, "score": {"passed": passed},
            "agent": {"wall_seconds": wall, "num_turns": turns, "output_tokens": 1000,
                      "total_cost_usd": 0.5, "is_error": False, "subtype": "success"},
            "changes": {"touched_tests": False, "touched_context_files": False}}


TRIALS = (
    [_trial("a", "with", True, 100, 10) for _ in range(3)]
    + [_trial("a", "without", False, 200, 20) for _ in range(3)]
    + [_trial("b", "with", True, 50, 5)] * 3
    + [_trial("b", "without", True, 100, 10)] * 3
)


def test_correctness_per_task_difference():
    c = correctness(TRIALS)
    assert c["with"]["rate"] == 1.0 and c["without"]["rate"] == 0.5
    assert c["tasks_compared"] == 2
    assert math.isclose(c["mean_diff"], 0.5)
    assert c["helped"] == 1 and c["hurt"] == 0


def test_efficiency_ratio_is_with_over_without():
    e = efficiency(TRIALS, "wall_seconds")
    assert math.isclose(e["ratio"], 0.5)
    assert e["tasks_compared"] == 2


def test_report_renders():
    text = report(TRIALS, [], "Test")
    assert "| with |" in text and "Per-task difference" in text
    assert tails(TRIALS)["with"]["agent_error"] == 0.0


def test_parse_transcript_reads_init_and_result():
    lines = [
        '{"type":"system","subtype":"init","model":"claude-x","permissionMode":"bypassPermissions"}',
        '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"python -m pytest -q"}}]}}',
        '{"type":"result","subtype":"success","is_error":false,"num_turns":7,"duration_ms":1234,'
        '"total_cost_usd":0.42,"usage":{"input_tokens":10,"output_tokens":20}}',
    ]
    m = parse_transcript("\n".join(lines))
    assert m["model"] == "claude-x" and m["num_turns"] == 7 and m["output_tokens"] == 20
    assert m["tool_counts"] == {"Bash": 1} and m["ran_tests"] is True


def test_agent_command_never_uses_bare_and_unsets_api_keys():
    study = StudyConfig("claude-code", "stable", "sonnet", 60, 30, ["with", "without"], 3, 1,
                        2, "4g", "anthropic", Path("data"), Path("prompts/task.txt"))
    cmd = build_command(study)
    assert "--bare" not in cmd
    assert "-u ANTHROPIC_API_KEY" in cmd and "--permission-mode bypassPermissions" in cmd
    assert "--strict-mcp-config" in cmd and "--max-turns 60" in cmd
