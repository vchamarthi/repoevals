# Contributing to repoevals

Thank you for helping improve repoevals. This guide covers how to report issues, set up a development environment,
make a change and get it merged.

By participating you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Ways to contribute

- **Report a bug** — open an issue with the bug report template. Include the `repoevals --version` output, your
  operating system, Python version, the command you ran and the relevant part of `grade.json` or the error output.
- **Request a feature** — open an issue with the feature request template and describe the problem before the
  solution.
- **Report a security problem** — do not open an issue; follow [SECURITY.md](SECURITY.md).
- **Improve documentation** — documentation changes are as welcome as code changes.
- **Add a grader, test adapter or agent integration** — open an issue first so the design can be agreed before you
  invest time.
- **Contribute a starter configuration** — add `examples/starter-configs/<project>.repoevals.toml` and a row in its
  README.

Issues labelled `good first issue` are a good place to start.

## Development setup

Requirements: Python 3.10+, git.

```bash
git clone https://github.com/vchamarthi/repoevals.git
cd repoevals
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Configure a git identity if you have not already; the tests create git repositories.

## Checks

Run these before opening a pull request. CI runs the same checks.

```bash
ruff check src tests                # lint and import order
python -m pytest -q                 # unit and end-to-end tests
python -m build                     # the package builds
```

End-to-end tests run every task of the example repository in `examples/minisci` through the real CLI code paths.
To try a change by hand:

```bash
cp -R examples/minisci /tmp/minisci && cd /tmp/minisci
git init -q && git add -A && git commit -qm init
repoevals validate && repoevals check
```

## Making a change

1. Fork the repository and create a branch from `main` (`fix/review-matching`, `feat/ctest-junit`).
2. Keep each pull request focused on one change.
3. Add or update tests:
   - every bug fix comes with a test that fails without the fix;
   - every new grader or adapter comes with an end-to-end test and, where it can be gamed, a test showing the
     exploit is rejected (see `tests/test_hardening.py`).
4. Update the documentation in `docs/` and `README.md` when behaviour, options or output change.
5. Add an entry under **Unreleased** in [CHANGELOG.md](CHANGELOG.md).
6. Open a pull request and fill in the template.

### Commit messages

Use the imperative mood and explain why, not only what:

```
Count skipped hidden tests as failures

A conftest.py that marks every test as skipped made pytest exit 0, which
the pytest adapter treated as a pass.
```

### Code style

- Python 3.10+ with type hints on public functions; no runtime dependencies beyond the standard library (and
  `tomli` on Python 3.10).
- Line length 120, enforced by ruff.
- Docstrings explain behaviour and the reasoning behind non-obvious decisions; comments are for the why.
- Graders must stay deterministic: no network access and no language-model calls in grading paths.
- Anything an agent controls (answers, files in the workspace, trace contents) is untrusted input: validate it,
  never pass it to a shell unquoted, and never let it change what is compared.

### Compatibility

The task format (`task.toml`), the configuration (`repoevals.toml`), `grade.json` (`repoevals.grade/v1`), the CLI
and the GitHub Action inputs are public interfaces. Changes to them must be backward compatible, or be called out in
the changelog as breaking and released in a new minor version while the project is below 1.0.

## Review process

- A maintainer reviews every pull request, usually within a week.
- Pull requests need one approving review from a maintainer and passing CI to be merged.
- Changes to grading logic, workspace isolation or integrity checks need particular care: reviewers will ask how
  the change could be exploited by an agent.
- Maintainers squash-merge pull requests; the pull request title becomes the commit title.

## Licensing

repoevals is licensed under the Apache License 2.0. By submitting a contribution you agree that it is licensed under
the same terms, as described in section 5 of the [license](LICENSE). Only submit work you have the right to
contribute.
