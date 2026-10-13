You are reviewing a pull request to minisci. The change under review is the HEAD commit of this repository (inspect
it with `git show HEAD`). Review it as a maintainer would: report every problem that should be fixed before merge
(correctness, numerical robustness, API behaviour, hygiene). Do not report things that are acceptable, and do not
modify any files.

End your answer with a fenced ```json block containing an array of findings, each an object
`{"file": "<repo-relative path>", "line": <line in the new version of the file>, "severity": "blocker|major|minor",
"rule": "<short rule name>", "message": "<one sentence>"}`.
