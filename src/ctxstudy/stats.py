"""Small, dependency-free statistics. Each function is short enough to check by hand.

Why these:
  - Wilson interval: honest uncertainty for a pass rate, even near 0% or 100%.
  - Task-level (cluster) bootstrap: attempts on the same task are not independent,
    so we resample tasks, not trials.
  - Sign test across tasks: "on how many tasks did the file help vs hurt?",
    with an exact p-value and no distributional assumptions.
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Callable, Sequence

Z95 = 1.959963984540054
NAN = float("nan")


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    if n == 0:
        return (NAN, NAN)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def sign_test(pos: int, neg: int) -> float:
    """Two-sided exact binomial test of pos vs neg (ties dropped beforehand)."""
    n = pos + neg
    if n == 0:
        return 1.0
    k = min(pos, neg)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def bootstrap_ci(items: Sequence, stat: Callable[[list], float], b: int = 5000,
                 seed: int = 0, alpha: float = 0.05) -> tuple[float, float]:
    if not items:
        return (NAN, NAN)
    rng = random.Random(seed)
    n = len(items)
    values = sorted(stat([items[rng.randrange(n)] for _ in range(n)]) for _ in range(b))
    return (values[int(alpha / 2 * b)], values[int((1 - alpha / 2) * b) - 1])


def mean(xs: Sequence[float]) -> float:
    return statistics.fmean(xs) if xs else NAN


def median(xs: Sequence[float]) -> float:
    return statistics.median(xs) if xs else NAN


def geo_mean(xs: Sequence[float]) -> float:
    xs = [x for x in xs if x and x > 0]
    return math.exp(statistics.fmean(math.log(x) for x in xs)) if xs else NAN
