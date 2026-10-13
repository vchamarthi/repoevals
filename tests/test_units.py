import json
import math

import pytest

from repoevals.graders.review import gate_pass, parse_findings
from repoevals.stats import cluster_bootstrap, paired_compare, pass_at_k, pass_hat_k
from repoevals.trace import from_claude_stream, from_codex_json, process_metrics
from repoevals.util import glob_match, last_json


def test_glob_match():
    assert glob_match("a/b/AGENTS.md", ["**/AGENTS.md"])
    assert glob_match("AGENTS.md", ["**/AGENTS.md"])
    assert glob_match(".github/instructions/x.md", [".github/instructions/*.md"])
    assert not glob_match(".github/instructions/a/x.md", [".github/instructions/*.md"])
    assert glob_match("tests/unit/test_x.py", ["tests/**"])


def test_last_json_prefers_last_fenced_block():
    text = 'first ```json\n[1]\n``` then ```json\n{"targets": ["a"]}\n```'
    assert last_json(text) == {"targets": ["a"]}
    assert last_json("no json here") is None
    assert last_json('trailing [{"a": 1}]') == [{"a": 1}]


def test_pass_at_k():
    assert pass_at_k(10, 0, 1) == 0
    assert pass_at_k(10, 10, 3) == 1
    assert pass_at_k(4, 1, 1) == pytest.approx(0.25)
    assert pass_hat_k(4, 2, 2) == pytest.approx(1 / 6)


def test_cluster_bootstrap_bounds():
    m, lo, hi = cluster_bootstrap({"a": [True, True], "b": [False, False], "c": [True, False]})
    assert m == pytest.approx(0.5)
    assert 0 <= lo <= m <= hi <= 1


def test_paired_compare_detects_consistent_gain():
    a = {f"t{i}": [False, False] for i in range(10)}
    b = {f"t{i}": [True, True] for i in range(10)}
    r = paired_compare(a, b)
    assert r["delta"] == 1.0 and r["p_value"] < 0.01 and r["tasks_better"] == 10


def test_gate_pass_expressions():
    out = {"recall": 0.6, "precision": 0.4, "decoys_flagged": []}
    assert gate_pass(out, ["recall>=0.5", "decoys_flagged<=0"]) is True
    assert gate_pass(out, ["precision>=0.5"]) is False
    assert gate_pass(out, []) is None


def test_parse_findings_normalizes_paths():
    f = parse_findings('```json\n[{"file": "/tmp/run/workspace/pkg/a.py", "line": "7", "message": "m"}]\n```')
    assert f[0]["file"] == "pkg/a.py" and f[0]["line"] == 7


def test_claude_stream_converter():
    lines = [
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "1", "name": "Bash", "input": {"command": "pytest -q"}},
            {"type": "tool_use", "id": "2", "name": "Read", "input": {"file_path": "/w/workspace/AGENTS.md"}}]}}),
        json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "1", "is_error": True, "content": "1 failed"}]}}),
        json.dumps({"type": "result", "result": "done", "total_cost_usd": 0.12,
                    "usage": {"input_tokens": 100, "output_tokens": 20}}),
    ]
    events, answer = from_claude_stream(lines)
    assert answer == "done"
    m = process_metrics(events, ["**/AGENTS.md"], r"pytest")
    assert m["n_commands"] == 1 and m["n_command_fail"] == 1 and m["n_build_cmds"] == 1
    assert m["guidance_read"] == ["AGENTS.md"]
    assert m["cost_usd"] == pytest.approx(0.12) and m["input_tokens"] == 100


def test_codex_converter():
    lines = [json.dumps({"type": "item.completed", "item": {"type": "command_execution", "command": "ls",
                                                             "exit_code": 0, "aggregated_output": "x"}}),
             json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "final"}}),
             json.dumps({"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 2}})]
    events, answer = from_codex_json(lines)
    assert answer == "final" and events[0]["kind"] == "command"


def test_numeric_runner_ulp():
    np = pytest.importorskip("numpy")
    from repoevals.data.numeric_runner import default_compare, ulp_distance
    a = np.array([1.0], dtype=np.float64)
    b = np.nextafter(a, 2)
    assert ulp_distance(np, a, b) == 1
    r = default_compare(np, {}, np.array([1.0, math.nan]), np.array([1.0 + 1e-15, math.nan]), {})
    assert r["ok"] and r["special_values_ok"]
    r = default_compare(np, {}, np.array([math.inf]), np.array([1000.0]), {})
    assert not r["ok"]
