# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `author fix --base` and `author mutants --base`: author tasks at a release (prebuilt binaries) instead of the
  fix's parent.
- Installed package versions are recorded in `run.json` and in `check --json` output.

### Changed

- `author fix` keeps only library files in `oracle.patch` (changelogs and docs conflict at other bases).
- `validate` accepts release tags as bases and explains how to fetch missing bases in forks.
- `check --json` writes `{"tasks": [...], "packages": {...}, "repoevals_version": ...}`.

## [0.2.0] - 2026-10-13

### Added

- Task authoring: `repoevals author mine`, `fix`, `review-replay`, `mutants`, `prune-mutants` and `approve`.
- Agent playbook (`docs/agent-playbook.md`) for Claude Code, Codex and other agents to author tasks for a repository,
  with `repoevals author playbook`, `prompt` and `install` (Claude Code skill, Codex custom prompt).
- Task status (`[metadata].status = "draft" | "ready"`); `run --include-drafts`; `list` shows status; `validate`
  rejects unfinished draft instructions and placeholder targets.
- `repoevals show` (one attempt: result, failing tests, integrity, diff), `repoevals doctor` (setup checks),
  `repoevals init --preset`, `repoevals author pin`; every graded attempt stores `agent.diff`.
- `run`: `--jobs`, `--resume`, `--retries`, `--max-cost`, `--label`, `--shuffle`; hardware requirements checked before
  the agent starts; one failing attempt no longer stops the matrix; `run.json` manifest; agent version, resolved
  model, source revision, task and guidance hashes recorded.
- Agent status (`ok`, `timeout`, `infra_error`): missing CLIs, authentication failures and rate limits are retried
  and recorded as `error` instead of failing the agent.
- Named guidance arms (`[arms.<name>]` with `remove`, `install`, `overlay`); `guidance:file=` installs the file as
  both `AGENTS.md` and `CLAUDE.md`.
- `[environment].setup` (build once per workspace), `[environment].submodules`, `[environment].env_passthrough`,
  `[test].rebuild_globs`.
- Reports: per-family pass rates, median agent time, task health, configurations without a trace marked, cost shown
  as `n/a` when unknown, `report --fail-under`.
- Integrity checks: `source_access` and `meta_testing`.
- macOS support for CPU feature requirements.

### Changed

- Run directories default to `~/.cache/repoevals/runs/<repo>/<timestamp>` (or `$REPOEVALS_RUNS_DIR`); `--out` inside
  the repository is refused unless `--allow-out-in-repo` is given.
- `init` no longer creates an example task.
- `--docker` mounts the workspace at the same path, runs as the host user, passes API credentials from
  `[environment].env_passthrough`, and defaults to the `bridge` network.
- Grading uses the copy of the task taken when the attempt was prepared.
- Mutation testing: timeouts no longer count as kills (`timeout_kills = true` restores it).
- Benchmarks are timed by repoevals (`trust_bench_output = true` to use the benchmark's own measurement); speedups
  use paired runs in random order.
- Review findings without a message earn no credit.
- Build artifacts must be structurally valid and at least `artifact_min_bytes`.
- Starter configurations build once per workspace and include `CLAUDE.md` and `.claude/` in their guidance globs.

### Fixed

- `action.yml` was not valid YAML.
- Hidden numerical references were reachable from the candidate process.
- A `pytest.py` (or `sitecustomize.py`, `*.pth`) in the workspace could fake a passing test run.
- Task ids could contain path separators.
- Builds, tests and agents received all credentials from the environment.
- Action inputs were interpolated into shell scripts; task selections were glob-expanded.
- Unbounded command output was held in memory.
- FIFOs in a workspace could block grading.
- Configurations with only score-based tasks were missing from reports; unreadable grades were silently dropped.

## [0.1.0] - 2026-10-13

### Added

- Task format (`task.toml`, `instruction.md`, setup and reference patches, hidden files) and repository
  configuration (`repoevals.toml`).
- Graders: `hidden_tests`, `command`, `mutation`, `review_seeded`, `review_replay`, `numeric` and `perf`, with
  plugin support for repository-specific and packaged graders.
- Test adapters for pytest, Bazel, CTest and arbitrary commands.
- History-free workspaces with leak checks; grading against a protected copy of the workspace history.
- Integrity checks for test tampering, protected files, git-history mining, network use and grader probing.
- `repoevals check` task self-check: null run, repeated reference solution, mutant validity and review flooding.
- Agent runners for Claude Code, Codex and arbitrary command-line agents, with optional container isolation and
  guidance arms.
- Reports with task-clustered confidence intervals, paired comparisons, pass@k, pass^k, cost and tokens.
- Harbor task export and agent guidance lint.
- GitHub Action and example repository.

[Unreleased]: https://github.com/vchamarthi/repoevals/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/vchamarthi/repoevals/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/vchamarthi/repoevals/releases/tag/v0.1.0
