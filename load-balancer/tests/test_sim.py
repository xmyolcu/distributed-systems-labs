import pytest

from lbsim import simulate


def test_mm1_matches_queueing_theory():
    # Tek sunucu, Poisson varış, üstel servis = M/M/1.
    # mu = 100/sn (10 ms), lambda = 70/sn  ->  E[T] = 1 / (mu - lambda) = 33.3 ms
    results = [simulate("rr", [1.0], 70.0, n_requests=200_000, seed=s).resp_mean_ms
               for s in range(3)]
    mean = sum(results) / len(results)
    assert mean == pytest.approx(1000 / 30, rel=0.05)


def test_round_robin_splits_evenly():
    r = simulate("rr", [1, 1, 1, 0.5], 200.0, n_requests=40_000, seed=1)
    assert all(s == pytest.approx(0.25, abs=1e-4) for s in r.share)


def test_least_connections_avoids_slow_server():
    # Toplam kapasite 350/sn, yük %70.
    r = simulate("lc", [1, 1, 1, 0.5], 245.0, n_requests=50_000, seed=2)
    assert r.share[3] < 0.2          # RR olsa 0.25 olurdu


def test_same_seed_same_result():
    a = simulate("p2c", [1, 1, 1, 1], 300.0, n_requests=10_000, seed=7)
    b = simulate("p2c", [1, 1, 1, 1], 300.0, n_requests=10_000, seed=7)
    assert a == b


def test_unknown_policy_raises():
    with pytest.raises(ValueError):
        simulate("random", [1, 1], 10.0, n_requests=100)