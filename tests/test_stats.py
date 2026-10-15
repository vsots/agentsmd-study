import math

from ctxstudy.stats import bootstrap_ci, geo_mean, mean, sign_test, wilson


def test_wilson_matches_textbook_value():
    lo, hi = wilson(5, 10)
    assert math.isclose(lo, 0.2366, abs_tol=1e-3)
    assert math.isclose(hi, 0.7634, abs_tol=1e-3)


def test_wilson_handles_extremes():
    lo, hi = wilson(0, 20)
    assert lo == 0.0 and 0.1 < hi < 0.2
    lo, hi = wilson(20, 20)
    assert hi == 1.0 and 0.8 < lo < 0.9
    assert all(math.isnan(x) for x in wilson(0, 0))


def test_sign_test_exact_values():
    assert math.isclose(sign_test(5, 0), 2 / 32)
    assert sign_test(3, 3) == 1.0
    assert sign_test(0, 0) == 1.0


def test_bootstrap_is_deterministic_and_brackets_the_mean():
    data = [0.0, 0.1, 0.2, -0.1, 0.3, 0.0, 0.1]
    ci1 = bootstrap_ci(data, mean, b=2000, seed=1)
    ci2 = bootstrap_ci(data, mean, b=2000, seed=1)
    assert ci1 == ci2
    assert ci1[0] <= mean(data) <= ci1[1]


def test_geo_mean_ignores_nonpositive():
    assert math.isclose(geo_mean([1.0, 4.0]), 2.0)
    assert math.isclose(geo_mean([2.0, 0.0, -1.0]), 2.0)
