"""Harness-independent agent trace: `<run_dir>/events.jsonl`, one JSON object per line.

    {"kind": "command", "text": "pytest tests/x.py", "rc": 1, "output": "...", "denied": false}
    {"kind": "read",    "path": "dpnp/linalg.py"}
    {"kind": "edit",    "path": "dpnp/linalg.py"}
    {"kind": "message", "text": "final or intermediate assistant text"}
    {"kind": "usage",   "input_tokens": 1200, "output_tokens": 300, "cached_tokens": 0, "cost_usd": 0.01}

Converters turn agent CLI transcripts (Claude Code stream-json, Codex exec --json) into this format, so graders and
reports never parse a vendor format. Unknown lines are skipped, never fatal.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .util import glob_match


def read_events(run_dir: Path) -> list[dict]:
    p = run_dir / "events.jsonl"
    out = []
    if p.exists():
        for line in p.read_text(errors="replace").splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if isinstance(ev, dict):
                out.append(ev)
    return out


def write_events(run_dir: Path, events: list[dict]) -> None:
    with open(run_dir / "events.jsonl", "w") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_text_of(c.get("text", c.get("content", ""))) if isinstance(c, dict) else str(c)
                         for c in content)
    return str(content or "")


def from_claude_stream(lines: list[str]) -> tuple[list[dict], str]:
    """Claude Code `-p --output-format stream-json --verbose`. Returns (events, final answer)."""
    events, pending, answer = [], {}, ""
    for line in lines:
        try:
            m = json.loads(line)
        except ValueError:
            continue
        typ = m.get("type")
        if typ == "assistant":
            for c in m.get("message", {}).get("content", []) or []:
                if c.get("type") == "tool_use":
                    name, inp = c.get("name", ""), c.get("input", {}) or {}
                    if name == "Bash":
                        ev = {"kind": "command", "text": inp.get("command", "")}
                    elif name in ("Read", "NotebookRead"):
                        ev = {"kind": "read", "path": inp.get("file_path", "")}
                    elif name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                        ev = {"kind": "edit", "path": inp.get("file_path", inp.get("notebook_path", ""))}
                    elif name in ("Grep", "Glob"):
                        ev = {"kind": "search", "text": json.dumps(inp)}
                    elif name in ("WebFetch", "WebSearch"):
                        ev = {"kind": "network", "text": json.dumps(inp)}
                    else:
                        ev = {"kind": "tool", "name": name, "text": json.dumps(inp)[:2000]}
                    events.append(ev)
                    pending[c.get("id")] = ev
                elif c.get("type") == "text" and c.get("text"):
                    events.append({"kind": "message", "text": c["text"]})
        elif typ == "user":
            for c in m.get("message", {}).get("content", []) or []:
                if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("tool_use_id") in pending:
                    ev = pending.pop(c["tool_use_id"])
                    out = _text_of(c.get("content"))
                    ev["output"] = out[-20000:]
                    if c.get("is_error"):
                        ev["rc"] = ev.get("rc", 1)
                        if "permission" in out.lower() or "denied" in out.lower():
                            ev["denied"] = True
                    else:
                        ev.setdefault("rc", 0)
        elif typ == "result":
            answer = m.get("result") or answer
            u = m.get("usage", {}) or {}
            events.append({"kind": "usage", "input_tokens": u.get("input_tokens", 0),
                           "output_tokens": u.get("output_tokens", 0),
                           "cached_tokens": u.get("cache_read_input_tokens", 0),
                           "cost_usd": m.get("total_cost_usd"), "turns": m.get("num_turns")})
    return events, answer


def from_codex_json(lines: list[str]) -> tuple[list[dict], str]:
    """Codex CLI `codex exec --json` (JSONL thread events). Best effort across CLI versions."""
    events, answer = [], ""
    for line in lines:
        try:
            m = json.loads(line)
        except ValueError:
            continue
        typ = m.get("type", "")
        item = m.get("item", {}) or {}
        it = item.get("type") or item.get("item_type")
        if typ == "item.completed" and it == "command_execution":
            events.append({"kind": "command", "text": item.get("command", ""), "rc": item.get("exit_code"),
                           "output": (item.get("aggregated_output") or "")[-20000:]})
        elif typ == "item.completed" and it == "file_change":
            for ch in item.get("changes", []) or []:
                events.append({"kind": "edit", "path": ch.get("path", "")})
        elif typ == "item.completed" and it in ("agent_message", "assistant_message"):
            answer = item.get("text", answer)
            events.append({"kind": "message", "text": answer})
        elif typ == "item.completed" and it == "web_search":
            events.append({"kind": "network", "text": item.get("query", "")})
        elif typ == "turn.completed":
            u = m.get("usage", {}) or {}
            events.append({"kind": "usage", "input_tokens": u.get("input_tokens", 0),
                           "output_tokens": u.get("output_tokens", 0),
                           "cached_tokens": u.get("cached_input_tokens", 0), "cost_usd": None})
    return events, answer


CONVERTERS = {"claude": from_claude_stream, "codex": from_codex_json}


def process_metrics(events: list[dict], guidance_globs: list[str], build_regex: str = "") -> dict:
    """Numbers about how the agent worked, attached to every grade (never part of pass/fail by themselves)."""
    cmds = [e for e in events if e.get("kind") == "command"]
    usage = [e for e in events if e.get("kind") == "usage"]
    builds = [e for e in cmds if build_regex and re.search(build_regex, e.get("text", ""))]
    costs = [e.get("cost_usd") for e in usage if e.get("cost_usd") is not None]

    def failed(e):
        return bool(e.get("denied")) or e.get("rc") not in (0, None)

    def rel(p: str) -> str:
        p = p.replace("\\", "/")
        return p.split("/workspace/", 1)[1] if "/workspace/" in p else p.lstrip("/")
    return {
        "tool_calls": sum(e.get("kind") not in ("message", "usage") for e in events),
        "n_commands": len(cmds),
        "n_command_fail": sum(map(failed, cmds)),
        "n_build_cmds": len(builds),
        "n_build_fail": sum(map(failed, builds)),
        "n_denied": sum(bool(e.get("denied")) for e in cmds),
        "n_edits": sum(e.get("kind") == "edit" for e in events),
        "guidance_read": sorted({rel(e["path"]) for e in events if e.get("kind") == "read" and e.get("path")
                                 and glob_match(rel(e["path"]), guidance_globs)}),
        "input_tokens": sum(int(e.get("input_tokens") or 0) for e in usage),
        "output_tokens": sum(int(e.get("output_tokens") or 0) for e in usage),
        "cost_usd": round(sum(costs), 6) if costs else None,
    }
