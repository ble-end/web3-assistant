from assistant.usage.ratelimit import RateLimiter


def test_allows_up_to_the_limit_then_blocks_and_then_recovers():
    now = [0.0]
    limiter = RateLimiter(2, 60, clock=lambda: now[0])
    assert limiter.check("a") == 0 and limiter.check("a") == 0
    assert limiter.check("a") > 0
    now[0] = 61
    assert limiter.check("a") == 0


def test_users_are_limited_separately():
    limiter = RateLimiter(1, 60, clock=lambda: 0.0)
    assert limiter.check("a") == 0 and limiter.check("b") == 0
    assert limiter.check("a") > 0
