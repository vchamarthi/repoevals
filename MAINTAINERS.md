# Maintainers

## Current maintainers

| maintainer | areas |
|---|---|
| [@vchamarthi](https://github.com/vchamarthi) | lead maintainer, releases |

## Responsibilities

Maintainers:

- triage new issues within a week: label, ask for missing information, or close with an explanation;
- review pull requests and keep review turnaround under a week;
- keep `main` releasable: CI green, changelog current;
- cut releases following the process below;
- handle security reports privately, following [SECURITY.md](SECURITY.md);
- enforce the [Code of Conduct](CODE_OF_CONDUCT.md).

## Reviewing pull requests

Before approving, check that:

- CI passes on all jobs;
- the change has tests, including a test that fails without it for bug fixes;
- grading stays deterministic and makes no network or language-model calls;
- agent-controlled input (answers, workspace files, traces) cannot reach a shell unquoted, write outside the
  workspace, or change the comparison — ask "how would an agent exploit this?";
- public interfaces (`task.toml`, `repoevals.toml`, `grade.json`, CLI, Action inputs) stay backward compatible, or
  the break is intended and recorded in the changelog;
- documentation and `CHANGELOG.md` are updated.

Merge with **squash and merge**. Do not merge your own pull request without a second maintainer's approval when one
is available; a sole maintainer may merge their own changes after CI passes.

## Labels

| label | use |
|---|---|
| `bug` | something does not work as documented |
| `enhancement` | new feature or improvement |
| `documentation` | documentation only |
| `grader` | grading logic |
| `security` | security-sensitive change (never for undisclosed vulnerabilities) |
| `breaking` | changes a public interface |
| `good first issue` | small, well-described, suitable for new contributors |
| `needs-info` | waiting for the reporter |

## Versioning

repoevals follows [Semantic Versioning](https://semver.org). Before 1.0, a minor version (0.x.0) may contain breaking
changes, which must be listed under **Changed** or **Removed** in the changelog; patch versions (0.x.y) contain only
fixes. The `grade.json` schema version (`repoevals.grade/v1`) changes only when existing fields change meaning.

## One-time repository setup

- **Branch protection on `main`:** require pull requests, one approval, and the `lint`, `test`, `build` and `action`
  checks.
- **Private vulnerability reporting:** enabled under *Settings → Code security*.
- **Discussions:** enabled, used for questions and ideas.
- **Distribution:** releases are published on GitHub only (wheel and source archive); users install from the
  release tag. The project is not published to PyPI.

## Release process

1. Make sure `main` is green and every merged change is in `CHANGELOG.md` under **Unreleased**.
2. Open a release pull request that:
   - sets `version` in `pyproject.toml` and `__version__` in `src/repoevals/__init__.py`;
   - moves the **Unreleased** entries to a new `## [X.Y.Z] - YYYY-MM-DD` section;
   - updates version references in `README.md` and `docs/` (`@vX.Y.Z`).
3. After merging, tag the merge commit and push the tag:

   ```bash
   git checkout main && git pull
   git tag -a vX.Y.Z -m "repoevals X.Y.Z"
   git push origin vX.Y.Z
   ```

4. The `release` workflow builds the package, checks that the tag matches the package version, creates the GitHub
   release with the changelog section, wheel and source archive.
5. Move the major-version tag used by Action users (`v0`, later `v1`) to the new release:

   ```bash
   git tag -f v0 vX.Y.Z && git push -f origin v0
   ```

6. Verify the release: `pip install repoevals==X.Y.Z` in a clean environment and run `repoevals --version`.

If a release is broken, publish a fixed patch release; do not delete or replace published versions.

## Security releases

Fix security issues in a private fork or a GitHub security advisory, release a patch version, then publish the
advisory. Follow the timeline in [SECURITY.md](SECURITY.md).

## Becoming a maintainer

Contributors with a record of high-quality pull requests and reviews may be invited by the existing maintainers.
Maintainers who are inactive for six months may be moved to emeritus status, and can return at any time.
