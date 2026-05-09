import random
import statistics

from backend.welford import WelfordStats


def test_welford_matches_naive_mean_and_variance():
    rng = random.Random(0xECA0)
    values = [rng.gauss(50.0, 7.5) for _ in range(2000)]

    w = WelfordStats()
    for v in values:
        w.update(v)

    assert abs(w.mean - statistics.mean(values)) < 1e-6
    # `pvariance` is the population variance, which is what Welford computes
    # by default (M2 / n, not M2 / (n-1)).
    assert abs(w.variance - statistics.pvariance(values)) < 1e-6


def test_welford_handles_single_sample():
    w = WelfordStats()
    w.update(42.0)
    assert w.n == 1
    assert w.mean == 42.0
    assert w.variance == 0.0


def test_welford_zero_samples():
    w = WelfordStats()
    assert w.n == 0
    assert w.variance == 0.0
    assert w.std == 0.0
