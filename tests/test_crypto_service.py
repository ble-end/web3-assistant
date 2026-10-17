import asyncio

import httpx
import pytest

from assistant.tools.crypto.service import ResearchError, TokenService
from crypto_samples import ADDR, SOL_ADDR, goplus, goplus_solana, pair, sol_pair


class FakeResponse:
    def __init__(self, status=200, payload=None, bad_json=False):
        self.status_code, self._payload, self._bad = status, payload, bad_json

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._payload


class FakeClient:
    def __init__(self, route):
        self.route, self.calls = route, []

    async def get(self, url, params=None):
        self.calls.append(url)
        result = self.route(url, params)
        if isinstance(result, Exception):
            raise result
        return result

    async def aclose(self):
        pass


def default_route(url, params):
    if "api.dexscreener.com" in url:
        return FakeResponse(payload=[pair()] if "/tokens/v1/base/" in url else [])
    return FakeResponse(payload={"code": 1, "result": {ADDR: goplus()}})


def run(coro):
    return asyncio.run(coro)


def service(route=default_route, **kw):
    client = FakeClient(route)
    return TokenService(client=client, **kw), client


def test_full_lookup_finds_chain_and_combines_both_sources():
    svc, client = service()
    report = run(svc.lookup(ADDR.upper().replace("0X", "0x")))
    assert report.chain.key == "base" and report.market and report.security
    assert sum("dexscreener" in c for c in client.calls) == 3  # one call per chain
    assert sum("gopluslabs" in c for c in client.calls) == 1  # only for the chain where it trades


def test_invalid_address_never_touches_the_network():
    svc, client = service()
    for bad in ("0x123", "not an address", "", ADDR + "ff"):
        with pytest.raises(ResearchError):
            run(svc.lookup(bad))
    assert client.calls == []


def test_unsupported_chain_is_refused():
    svc, client = service()
    with pytest.raises(ResearchError):
        run(svc.lookup(ADDR, "polygon"))
    assert client.calls == []


def test_explicit_chain_makes_a_single_market_call():
    svc, client = service()
    run(svc.lookup(ADDR, "base"))
    assert sum("dexscreener" in c for c in client.calls) == 1


def test_one_chain_timing_out_does_not_break_the_others():
    def route(url, params):
        if "/tokens/v1/bsc/" in url:
            return httpx.TimeoutException("slow")
        return default_route(url, params)

    svc, _ = service(route)
    report = run(svc.lookup(ADDR))
    assert report.found and any("BNB Chain" in e and "too long" in e for e in report.errors)


def test_results_with_problems_are_not_cached():
    def route(url, params):
        if "/tokens/v1/bsc/" in url:
            return httpx.TimeoutException("slow")
        return default_route(url, params)

    svc, client = service(route)
    run(svc.lookup(ADDR))
    first = len(client.calls)
    run(svc.lookup(ADDR))
    assert len(client.calls) > first


def test_clean_results_are_cached():
    svc, client = service()
    run(svc.lookup(ADDR))
    calls = len(client.calls)
    run(svc.lookup(ADDR))
    assert len(client.calls) == calls and svc.stats["cache_hits"] == 1


def test_cache_expires():
    now = [0.0]
    svc, client = service(clock=lambda: now[0], cache_seconds=60)
    run(svc.lookup(ADDR))
    calls = len(client.calls)
    now[0] = 120
    run(svc.lookup(ADDR))
    assert len(client.calls) > calls


def test_everything_down_gives_a_report_with_errors_not_a_crash():
    svc, _ = service(lambda url, params: httpx.ConnectError("down"))
    report = run(svc.lookup(ADDR))
    assert not report.found and len(report.errors) >= 3


def test_rate_limit_message_is_reported_plainly():
    def route(url, params):
        return FakeResponse(status=429) if "dexscreener" in url else default_route(url, params)

    report = run(service(route)[0].lookup(ADDR))
    assert any("too many requests" in e for e in report.errors)


def test_unreadable_reply_is_handled():
    def route(url, params):
        return FakeResponse(bad_json=True) if "dexscreener" in url else default_route(url, params)

    report = run(service(route)[0].lookup(ADDR))
    assert any("could not read" in e for e in report.errors)


def test_security_found_on_another_chain_when_no_trading_pairs():
    def route(url, params):
        if "dexscreener" in url:
            return FakeResponse(payload=[])
        return FakeResponse(payload={"code": 1, "result": {ADDR: goplus()}}) if url.endswith("/56") else FakeResponse(payload={"code": 1, "result": {}})

    report = run(service(route)[0].lookup(ADDR))
    assert report.chain.key == "bsc" and report.market is None and report.security


def test_global_limit_protects_the_providers():
    svc, _ = service(max_per_minute=1)
    run(svc.lookup(ADDR))
    with pytest.raises(ResearchError):
        run(svc.lookup("0x" + "ef" * 20))


def solana_route(url, params):
    if "api.dexscreener.com" in url:
        return FakeResponse(payload=[sol_pair()] if "/tokens/v1/solana/" in url else [])
    assert "/solana/token_security" in url  # Solana uses its own GoPlus endpoint
    return FakeResponse(payload={"code": 1, "result": {SOL_ADDR: goplus_solana()}})


def test_solana_lookup_uses_only_solana_endpoints_and_keeps_case():
    svc, client = service(solana_route)
    report = run(svc.lookup(SOL_ADDR))
    assert report.chain.key == "solana" and report.market and report.security
    assert len(client.calls) == 2
    assert all(SOL_ADDR in c or "solana/token_security" in c for c in client.calls)


def test_evm_address_never_queries_solana():
    svc, client = service()
    run(svc.lookup(ADDR))
    assert not any("/solana" in c for c in client.calls)


def test_address_and_chain_must_match():
    svc, client = service(solana_route)
    for address, chain in ((ADDR, "solana"), (SOL_ADDR, "base")):
        with pytest.raises(ResearchError):
            run(svc.lookup(address, chain))
    assert client.calls == []


def test_solana_security_reply_with_a_different_key_case_is_still_found():
    def route(url, params):
        if "dexscreener" in url:
            return FakeResponse(payload=[sol_pair()])
        return FakeResponse(payload={"code": 1, "result": {SOL_ADDR.lower(): goplus_solana()}})

    assert run(service(route)[0].lookup(SOL_ADDR)).security is not None
