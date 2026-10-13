# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/vchamarthi/repoevals/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/vchamarthi/repoevals/releases/tag/v0.1.0
