# Improving agent guidance

Agent guidance files — `AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md`, `.github/instructions/*.md`,
`GEMINI.md` — tell coding agents how your project works. repoevals measures whether they help, shows what agents
struggle with, drafts improvements, and proves whether a draft is better before you adopt it.

```
run with and without guidance ──► guidance suggest ──► draft ──► guidance verify ──► adopt if it helps
```

## 1. Collect evidence

Run your tasks with the current guidance and without it:

```bash
repoevals run --agent claude-code --arm guidance:raw --arm guidance:none --repeats 5 --out runs/baseline
repoevals report runs/baseline --compare arm
```

The paired comparison tells you whether the current guidance helps at all.

## 2. Find what to change

```bash
repoevals guidance suggest runs/baseline
```

This reads every graded run, including each agent command, its output and the files read, and reports problems
seen in at least two runs (`--min-runs`):

| evidence | example suggestion |
|---|---|
| commands that failed repeatedly | "`python -m pytest` failed in 6 runs: document the correct way to run it" |
| environment errors in command output | "no SYCL device was found: state the device selector to use" |
| what agents looked up before their first edit | "80% of runs looked up `setup.py` before changing anything" |
| tasks that do worse with the guidance than without | "Task `fix_x` passes less often with the guidance (0.2) than without it (0.8)" |
| integrity violations despite the guidance | "`test_tampering` happened in 3 runs: state the rule explicitly" |
| stale references | "`AGENTS.md` mentions `scripts/build.sh`, which does not exist" |
| files an agent would not read | "AGENTS.md exists but CLAUDE.md does not: Claude Code may not read the guidance" |

Output in `runs/baseline/guidance/` (or `--out`):

| file | content |
|---|---|
| `evidence.md` | the suggestions and the tables behind them |
| `evidence.json` | the same, machine-readable |
| `draft/` | proposed guidance files, laid out as in the repository |

No model is used unless you ask for one.

## 3. Draft the change

By default the draft is your current `AGENTS.md` plus a "Notes from evaluation runs" section listing the
evidence-backed points to address (and a `CLAUDE.md` containing `@AGENTS.md` if you have none). Edit it by hand, or
let an agent write it from the evidence:

```bash
repoevals guidance suggest runs/baseline --draft-with claude-code
repoevals guidance suggest runs/baseline --draft-with codex
repoevals guidance suggest runs/baseline --draft-with "cmd:my-llm-cli --stdin"
```

The drafting agent receives only the evidence and the current guidance files, must answer with the new file
contents, and may only write guidance files into the draft directory. Nothing in your repository changes.

## 4. Verify before adopting

```bash
repoevals guidance verify runs/baseline/guidance/draft --agent claude-code --repeats 5
```

This runs the same tasks with your current guidance and with the draft overlaid on it (arm
`guidance:overlay=<draft>`), then reports the paired difference:

```
draft vs guidance:raw: Δ pass rate +0.150 (95% CI +0.050 to +0.250, p=0.0312): helps
```

Verify on tasks the evidence did not come from, otherwise a draft that (accidentally) encodes hints about specific
tasks looks better than it is. Collect evidence on part of your tasks and verify on the rest:

```bash
repoevals run --agent claude-code --arm guidance:raw --arm guidance:none --repeats 5 --out runs/baseline tag:split-a
repoevals guidance suggest runs/baseline --draft-with claude-code
repoevals guidance verify runs/baseline/guidance/draft --agent claude-code --repeats 5 --exclude-tasks-in runs/baseline
```

Review every draft for task-specific hints before verifying; the drafting prompt forbids them, but check.

Adopt the draft only when it helps; "no clear difference" means more tasks or repeats are needed, or the change does
not matter. Copy the draft files into your repository and open a pull request with `verify.md` as evidence.

## Notes

- Agents read different files: Claude Code reads `CLAUDE.md`, Codex and most others read `AGENTS.md`, Copilot reads
  `.github/copilot-instructions.md`. A `CLAUDE.md` containing `@AGENTS.md` shares one source.
- Guidance outside the repository (`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`) applies to every arm;
  `repoevals doctor` lists it.
- Evidence comes from agent traces, so it is richest for Claude Code and Codex. `cmd:` agents produce no trace.
