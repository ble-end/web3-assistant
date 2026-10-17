"""Looks a token up across the supported chains. Caches briefly and limits total request volume."""
import asyncio
import logging
import time
from datetime import datetime, timezone

import httpx

from assistant.tools.crypto import providers
from assistant.tools.crypto.analysis import TokenReport, build_report, choose_primary_chain
from assistant.tools.crypto.chains import CHAINS, chain_names, chains_for, normalize_address
from assistant.usage.ratelimit import RateLimiter

log = logging.getLogger(__name__)


class ResearchError(Exception):
    """A problem that is safe to show to the user."""


class TokenService:
    def __init__(self, client=None, cache_seconds: float = 60.0, max_per_minute: int = 60, clock=time.monotonic):
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0), headers={"User-Agent": "web3-assistant/0.1"}
        )
        self._clock = clock
        self._cache_seconds = cache_seconds
        self._cache: dict = {}
        self._limiter = RateLimiter(max_per_minute, 60.0, clock)
        self.stats = {"lookups": 0, "cache_hits": 0, "dexscreener_calls": 0, "goplus_calls": 0}

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _pairs(self, chain, address):
        self.stats["dexscreener_calls"] += 1
        return await providers.fetch_pairs(self._client, chain, address)

    async def _security(self, chain, address):
        self.stats["goplus_calls"] += 1
        return await providers.fetch_security(self._client, chain, address)

    @staticmethod
    def _collect(chains, results, errors) -> dict:
        collected = {}
        for chain, result in zip(chains, results):
            if isinstance(result, providers.ProviderError):
                errors.append(f"{chain.name}: {result}")
            elif isinstance(result, BaseException):
                log.warning("Unexpected %s during token lookup", type(result).__name__)
                errors.append(f"{chain.name}: unexpected problem")
            else:
                collected[chain.key] = result
        return collected

    async def lookup(self, address: str, chain: str | None = None) -> TokenReport:
        addr = normalize_address(address)
        if addr is None:
            raise ResearchError("That does not look like a token address (EVM: 0x followed by 40 letters or numbers; Solana: 32 to 44 letters and numbers).")
        if chain is not None and chain not in CHAINS:
            raise ResearchError(f"I only support: {chain_names()}.")

        key = (addr, chain)
        cached = self._cache.get(key)
        if cached and self._clock() - cached[0] < self._cache_seconds:
            self.stats["cache_hits"] += 1
            return cached[1]

        wait = self._limiter.check("global")
        if wait > 0:
            raise ResearchError(f"I am handling a lot of lookups right now. Try again in about {int(wait) + 1} seconds.")

        self.stats["lookups"] += 1
        errors: list[str] = []
        available = chains_for(addr)
        if chain is not None and CHAINS[chain] not in available:
            raise ResearchError(f"That address format does not belong to {CHAINS[chain].name}.")
        chains = [CHAINS[chain]] if chain else available
        pair_results = await asyncio.gather(*(self._pairs(c, addr) for c in chains), return_exceptions=True)
        pairs_by_chain = self._collect(chains, pair_results, errors)

        primary = choose_primary_chain(addr, pairs_by_chain)
        candidates = [CHAINS[primary]] if primary else chains
        security_results = await asyncio.gather(*(self._security(c, addr) for c in candidates), return_exceptions=True)
        security_by_chain = self._collect(candidates, security_results, errors)

        report = build_report(addr, datetime.now(timezone.utc), pairs_by_chain, security_by_chain, errors)
        if not errors:  # never cache a result that had problems
            if len(self._cache) >= 200:
                self._cache.pop(min(self._cache, key=lambda k: self._cache[k][0]))
            self._cache[key] = (self._clock(), report)
        return report
