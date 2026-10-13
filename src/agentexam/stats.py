"""Statistics for agent evals, standard library only.

- pass@k (unbiased estimator, Chen et al. 2021) and pass^k (all k trials succeed, tau-bench) per task
- task-clustered bootstrap confidence intervals for mean pass rate (resample tasks, then trials;
  Miller 2024, "Adding Error Bars to Evals")
- paired comparison of two arms on the same tasks: mean per-task difference, bootstrap CI, and an exact /
  Monte-Carlo sign-flip permutation p-value
- minimum detectable effect for a planned number of tasks and trials
"""
from __future__ import annotations

import math
import random
from statistics import mean


def pass_at_k(n: int, c: int, k: int) -> float:
    """Probability that at least one of k samples (from n trials with c successes) passes."""
    if c == 0:
        return 0.0
    if n - c < k:
        return 1.0
    return 1.0 - math.prod((n - c - i) / (n - i) for i in range(k))


def pass_hat_k(n: int, c: int, k: int) -> float:
    """Probability that all of k samples pass (reliability): C(c,k)/C(n,k)."""
    if k > n:
        return float("nan")
    return math.comb(c, k) / math.comb(n, k)


def task_rates(results: dict[str, list[bool]]) -> dict[str, float]:
    return {t: sum(v) / len(v) for t, v in results.items() if v}


def cluster_bootstrap(results: dict[str, list[bool]], n_boot: int = 4000, alpha: float = 0.05,
                      seed: int = 0) -> tuple[float, float, float]:
    """(mean, lo, hi) of the macro pass rate, resampling tasks then trials within each task."""
    tasks = [t for t, v in results.items() if v]
    if not tasks:
        return float("nan"), float("nan"), float("nan")
    point = mean(sum(results[t]) / len(results[t]) for t in tasks)
    rng = random.Random(seed)
    stats = []
    for _ in range(n_boot):
        acc = 0.0
        for t in rng.choices(tasks, k=len(tasks)):
            trials = results[t]
            acc += sum(rng.choices(trials, k=len(trials))) / len(trials)
        stats.append(acc / len(tasks))
    stats.sort()
    return point, stats[int(n_boot * alpha / 2)], stats[int(n_boot * (1 - alpha / 2)) - 1]


def paired_compare(a: dict[str, list[bool]], b: dict[str, list[bool]], n_boot: int = 4000, seed: int = 0) -> dict:
    """Arm b minus arm a on the tasks both arms ran. Positive delta = b is better."""
    common = sorted(t for t in a if t in b and a[t] and b[t])
    if not common:
        return {"n_tasks": 0}
    d = [sum(b[t]) / len(b[t]) - sum(a[t]) / len(a[t]) for t in common]
    rng = random.Random(seed)
    boots = sorted(mean(rng.choices(d, k=len(d))) for _ in range(n_boot))
    obs = abs(mean(d))
    nz = [x for x in d if x != 0]
    if len(nz) <= 16:  # exact sign-flip test
        total = 2 ** len(nz)
        extreme = sum(abs(sum(x if (m >> i) & 1 else -x for i, x in enumerate(nz))) / len(d) >= obs - 1e-12
                      for m in range(total))
        p = extreme / total if nz else 1.0
    else:
        hits = sum(abs(sum(x if rng.random() < 0.5 else -x for x in nz)) / len(d) >= obs - 1e-12
                   for _ in range(20000))
        p = (hits + 1) / 20001
    return {"n_tasks": len(common), "delta": round(mean(d), 4),
            "ci95": [round(boots[int(n_boot * 0.025)], 4), round(boots[int(n_boot * 0.975) - 1], 4)],
            "p_value": round(p, 4), "tasks_better": sum(x > 0 for x in d), "tasks_worse": sum(x < 0 for x in d)}


def min_detectable_effect(n_tasks: int, trials: int, p: float = 0.5, between_task_sd: float = 0.3,
                          alpha_z: float = 1.96, power_z: float = 0.84) -> float:
    """Approximate MDE for a paired two-arm comparison (normal approximation)."""
    within = p * (1 - p) / max(trials, 1)
    var_diff = 2 * within + (between_task_sd ** 2) * 0.2  # assumes arms are strongly correlated per task
    return (alpha_z + power_z) * math.sqrt(var_diff / max(n_tasks, 1))
