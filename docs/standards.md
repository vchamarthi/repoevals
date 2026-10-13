# Methodology standards agentexam implements (and the ones it does not yet)

Sources and full discussion: [research/02-landscape.md](research/02-landscape.md).

## Task validity

| practice | source | agentexam |
|---|---|---|
| Every task solvable: the reference solution passes | ABC (Zhu et al. 2025); Terminal-Bench; SWE-bench harness validation | `agentexam check` oracle probe |
| The do-nothing agent fails | ABC; Terminal-Bench 3 vetting | `agentexam check` null probe |
| Flakiness measured, not hidden by retries | ABC; SWE-bench-Live | `check --repeats N`; hidden tests never retried |
| Graders reject known cheats | ImpossibleBench (2026); SWE-Bench Pro V2 | findings-spam probe; test-tampering and protected-file reset; history-mining detection |
| Mutants are valid and non-equivalent | mutation testing practice; Meta ACH | reference tests must kill `oracle_min_kill` (default 100%); build-breaking mutants excluded |
| Instructions do not leak the fix | oneDAL PR review; SWE-bench Verified audit | `validate` warns on SHAs, PR links, "FIXED in" |
| Hidden material never in the workspace | SWE-bench / Commit0 history leaks; Poolside 2026 | `git archive` snapshot, single root commit, reflog expired, leak scan |
| Grading in a pristine environment | SWE-Bench Pro V2 (fresh-image regrade) | **partial**: tree snapshot/restore + protected-file reset; container regrade planned |

## Outcome validity

| practice | source | agentexam |
|---|---|---|
| Passing tests ≠ correct (PatchDiff, UTBoost, EvalPlus) | ICSE 2026; ACL 2025 | hidden tests + regression suites; numeric probes check edge cases tests often miss |
| Review scored on precision as well as recall | ReviewBench 2026; oneDAL PR critique | one-to-one matching, precision/F1, decoys, clean controls, findings cap |
| Numerical tolerances explicit per dtype; NaN/Inf positions compared; reference pinned | numerical-software practice; dpnp#2914 | `numeric` grader with per-dtype rtol/atol, special-value check, ULP distance |
| Performance with realistic baselines and noise control | KernelBench-Verified 2026 | interleaved base/candidate runs, bootstrap CI on speedup |

## Reporting

| practice | source | agentexam |
|---|---|---|
| Multiple trials; pass@k and pass^k | Chen 2021; tau-bench | per-task pass@1/@3, pass^3 |
| Confidence intervals clustered by task | Miller 2024, *Adding Error Bars to Evals* | task-then-trial bootstrap |
| Paired comparison for A/B (same tasks) | Miller 2024 | paired deltas, exact sign-flip test |
| Do-nothing baseline reported | ABC | `--agent noop` |
| Cost and tokens next to accuracy | HAL, Anthropic 2026 guidance | `process.cost_usd`, tokens per run and per arm |
| Contamination: task dates vs model cutoff | SWE-rebench, LiveCodeBench | `[metadata].created` carried into every result |
| Read transcripts; investigate 0% and 100% tasks | Anthropic, *Demystifying evals for AI agents* (2026) | integrity flags list runs to read; events.jsonl kept per run |
| Infrastructure errors separated from failures | oneDAL PR; Inspect | `status: error` excluded from pass rates |

## Not yet implemented (tracked in the roadmap)

- Canary GUID strings in task files and a private, rotating holdout split.
- Inspect `.eval` log and Every-Eval-Ever export; ATIF trajectories.
- Container-isolated grading and fresh-image regrade.
- Eval cards (datasheet per task pack).
- LLM-judge rubrics, deliberately: everything above stays deterministic; judges may be added later only as a
  separately reported, versioned metric.
