# Security policy

## Supported versions

| version | supported |
|---|---|
| latest 0.x release | yes |
| older releases | no |

## Reporting a vulnerability

Please do not report security problems in public issues, discussions or pull requests.

Report them privately through GitHub: open the repository's **Security** tab and choose **Report a vulnerability**.
Include a description, the affected version, steps to reproduce and the impact you expect.

You will receive an acknowledgement within 5 working days. We aim to release a fix within 90 days, and will agree a
disclosure date with you and credit you in the advisory unless you prefer otherwise.

## Scope

In scope:

- an agent or task content escaping the workspace during preparation or grading, e.g. writing outside the run
  directory or executing commands through answers or file names;
- an agent obtaining hidden tests, reference solutions, mutants or review manifests;
- an agent influencing its own grade other than by solving the task (please also report these as regular bugs if
  they are not exploitable beyond the score);
- the GitHub Action exposing secrets or running untrusted code with elevated permissions.

Out of scope:

- agents running without `--docker` having the permissions of the user who started them — this is documented
  behaviour; use containers for untrusted agents and tasks;
- vulnerabilities in agent CLIs, test frameworks or projects being evaluated.
