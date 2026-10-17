"""Talks to the two free data providers. Everything they return is treated as untrusted."""
import httpx

from assistant.tools.crypto.chains import Chain

DEXSCREENER_BASE = "https://api.dexscreener.com"
GOPLUS_BASE = "https://api.gopluslabs.io/api/v1"


class ProviderError(Exception):
    """A provider problem, worded so it is safe to show to the user."""


async def _get_json(client: httpx.AsyncClient, url: str, provider: str, params=None):
    try:
        response = await client.get(url, params=params)
    except httpx.TimeoutException:
        raise ProviderError(f"{provider} took too long to answer") from None
    except httpx.HTTPError:
        raise ProviderError(f"{provider} could not be reached") from None
    if response.status_code == 429:
        raise ProviderError(f"{provider} says too many requests were made")
    if response.status_code >= 400:
        raise ProviderError(f"{provider} returned an error (HTTP {response.status_code})")
    try:
        return response.json()
    except ValueError:
        raise ProviderError(f"{provider} sent a reply I could not read") from None


async def fetch_pairs(client: httpx.AsyncClient, chain: Chain, address: str) -> list[dict]:
    """DexScreener: all trading pairs for a token on one chain (limit: 300 requests/minute)."""
    data = await _get_json(
        client, f"{DEXSCREENER_BASE}/tokens/v1/{chain.dexscreener_id}/{address}", "DexScreener"
    )
    pairs = data if isinstance(data, list) else (data.get("pairs") if isinstance(data, dict) else None)
    if pairs is None:  # a chain with no pairs may come back as null
        return []
    if not isinstance(pairs, list):
        raise ProviderError("DexScreener sent an unexpected reply")
    return [p for p in pairs if isinstance(p, dict)]


async def fetch_security(client: httpx.AsyncClient, chain: Chain, address: str) -> dict | None:
    """GoPlus: contract security data. Returns None if GoPlus has nothing for this address."""
    if chain.kind == "solana":
        url, key = f"{GOPLUS_BASE}/solana/token_security", address  # Solana addresses are case-sensitive
    else:
        url, key = f"{GOPLUS_BASE}/token_security/{chain.goplus_id}", address.lower()
    data = await _get_json(client, url, "GoPlus", params={"contract_addresses": address})
    if not isinstance(data, dict):
        raise ProviderError("GoPlus sent an unexpected reply")
    result = data.get("result")
    if not isinstance(result, dict):
        if data.get("code") not in (None, 1):
            raise ProviderError("GoPlus could not check this token right now")
        return None
    info = result.get(key)
    if info is None and chain.kind == "solana" and len(result) == 1:
        info = next(iter(result.values()))
    return info if isinstance(info, dict) and info else None
