import math

import pytest

from retrysim import Config, simulate

NO_TRIGGER = dict(trigger_factor=1.0, duration_s=40.0)


def test_healthy_system_succeeds_without_retries():
    r = simulate(Config(**NO_TRIGGER))
    assert r.success_rate > 0.99
    assert r.amplification == pytest.approx(1.0)


def test_no_retry_never_amplifies():
    r = simulate(Config(retry="none"))
    assert r.amplification == pytest.approx(1.0)


def test_max_retries_caps_amplification():
    r = simulate(Config(retry="immediate", max_retries=3))
    assert r.amplification <= 1 + 3 + 1e-9


def test_budget_caps_amplification():
    r = simulate(Config(retry="budget", budget_ratio=0.1))
    # 10 jetonluk başlangıç tamponu nedeniyle çok küçük bir pay
    assert r.amplification <= 1.1 + 0.01


def test_breaker_fails_fast_without_reaching_b():
    r = simulate(Config(retry="none", breaker=True))
    # Devre açıkken istekler B'ye gitmez: B'ye giden deneme sayısı kullanıcı isteğinden az.
    assert r.amplification < 1.0


def test_same_seed_same_result():
    a = simulate(Config(retry="backoff", seed=3))
    b = simulate(Config(retry="backoff", seed=3))
    assert a.success_rate == b.success_rate
    assert a.recovery_s == b.recovery_s or (math.isinf(a.recovery_s) and math.isinf(b.recovery_s))


def test_unknown_policy_raises():
    with pytest.raises(ValueError):
        simulate(Config(retry="forever"))